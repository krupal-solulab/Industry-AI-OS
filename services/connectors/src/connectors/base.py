"""Connector interface + built-in connectors.

Every connector — whether backed by an MCP server, Composio, or a direct client —
implements the same `Connector` interface: advertise `tools`, and `invoke(tool,
args)`. Callers never learn which backend a connector uses.
"""

from __future__ import annotations

import abc
from collections.abc import Callable
from dataclasses import dataclass, field

import httpx

from ai_os_shared.settings import get_settings


@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict = field(default_factory=dict)


class Connector(abc.ABC):
    key: str
    name: str
    kind: str  # "reference" | "mcp" | "composio"

    @property
    @abc.abstractmethod
    def tools(self) -> list[Tool]: ...

    @abc.abstractmethod
    async def invoke(self, tool: str, arguments: dict, config: dict) -> dict:
        """Run a tool. `config` is the tenant's stored connector config (creds etc.)."""


class EchoConnector(Connector):
    """Reference/mock connector proving the pattern end to end with no external deps."""

    key = "echo"
    name = "Echo (reference)"
    kind = "reference"

    @property
    def tools(self) -> list[Tool]:
        return [
            Tool(
                name="echo",
                description="Return the arguments back, verbatim. Proves the invoke path.",
                input_schema={"type": "object", "properties": {"message": {"type": "string"}}},
            ),
            Tool(
                name="ping",
                description="Health probe. Returns {'pong': true}.",
                input_schema={"type": "object", "properties": {}},
            ),
        ]

    async def invoke(self, tool: str, arguments: dict, config: dict) -> dict:
        if tool == "ping":
            return {"pong": True}
        if tool == "echo":
            return {"echo": arguments}
        raise ValueError(f"Unknown tool: {tool}")


class MicrosoftGraphConnector(Connector):
    """Placeholder for Microsoft Graph via an MCP server.

    Advertises real Graph-shaped tools but returns a not-configured response until a
    tenant supplies credentials/an MCP endpoint. This proves the registry + invoke
    contract for a *real* connector without shipping live credentials.
    """

    key = "microsoft-graph"
    name = "Microsoft 365 (Graph)"
    kind = "mcp"

    @property
    def tools(self) -> list[Tool]:
        return [
            Tool(
                name="list_messages",
                description="List recent mail messages for the signed-in user.",
                input_schema={"type": "object", "properties": {"top": {"type": "integer"}}},
            ),
            Tool(
                name="send_mail",
                description="Send an email on behalf of the user.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "to": {"type": "string"},
                        "subject": {"type": "string"},
                        "body": {"type": "string"},
                    },
                    "required": ["to", "subject", "body"],
                },
            ),
        ]

    async def invoke(self, tool: str, arguments: dict, config: dict) -> dict:
        mcp_endpoint = config.get("mcp_endpoint")
        if not mcp_endpoint:
            return {
                "status": "not_configured",
                "message": (
                    "Microsoft Graph connector is a placeholder. Configure an MCP "
                    "endpoint + credentials on this connector to enable live calls."
                ),
                "would_invoke": {"tool": tool, "arguments": arguments},
            }
        # When configured, calls would be proxied to the Graph MCP server here.
        raise NotImplementedError("Live Graph MCP proxying is enabled once configured.")


class ComposioConnector(Connector):
    """Placeholder for Composio-brokered connectors (hundreds of SaaS apps)."""

    key = "composio"
    name = "Composio (broker)"
    kind = "composio"

    @property
    def tools(self) -> list[Tool]:
        return [
            Tool(
                name="list_apps",
                description="List apps available via the tenant's Composio account.",
                input_schema={"type": "object", "properties": {}},
            )
        ]

    async def invoke(self, tool: str, arguments: dict, config: dict) -> dict:
        if not (config.get("composio_api_key") or get_settings().environment == "test"):
            return {
                "status": "not_configured",
                "message": "Set a Composio API key on this connector to enable it.",
            }
        raise NotImplementedError("Composio brokering is enabled once configured.")


# --------------------------------------------------------------------------- Nango
# Nango is NOT a business API — it provides OAuth + token refresh + an authenticated
# PROXY to each provider's OWN REST API. So a Nango connector is a THIN pass-through:
#   invoke(<HTTP method>, {"endpoint": "/vendor", "query": {...}, "body": {...}}, config)
# performs `<method> <provider>/<endpoint>` via Nango, which injects auth. The AI OS
# supplies all the business logic (validation, dedup, approval) *around* these calls.
#
# Sandbox mode (no NANGO_SECRET_KEY / connection id) returns provider-shaped fixtures
# flagged `_sandbox: true`, so a workflow runs end to end with NO accounts. Going live
# is a credentials change — the same connector, the same workflow definition, no code.

SandboxFn = Callable[[str, str, dict], dict]


class NangoConnector(Connector):
    kind = "nango"

    def __init__(self, provider: str, name: str, sandbox: SandboxFn | None = None) -> None:
        self.provider = provider
        self.key = f"nango.{provider}"
        self.name = name
        self._sandbox = sandbox

    @property
    def tools(self) -> list[Tool]:
        # The "tool" is the HTTP method; the endpoint/params live in the arguments.
        common = {
            "type": "object",
            "properties": {
                "endpoint": {"type": "string"},
                "query": {"type": "object"},
                "body": {"type": "object"},
            },
            "required": ["endpoint"],
        }
        return [
            Tool(name=m, description=f"{m} <endpoint> on {self.name} via Nango proxy.",
                 input_schema=common)
            for m in ("GET", "POST", "PUT", "PATCH", "DELETE")
        ]

    async def invoke(self, tool: str, arguments: dict, config: dict) -> dict:
        settings = get_settings()
        method = (tool or "GET").upper()
        endpoint = str(arguments.get("endpoint", ""))
        query = arguments.get("query") or None
        body = arguments.get("body") or None

        secret = config.get("nango_secret_key") or settings.nango_secret_key
        connection_id = config.get("connection_id")

        # ---- Sandbox (no live credentials): provider-shaped, clearly labeled -------
        if not secret or not connection_id:
            fixture = self._sandbox(method, endpoint, arguments) if self._sandbox else {}
            return {
                "status": "sandbox",
                "_sandbox": True,
                "provider": self.provider,
                "request": {"method": method, "endpoint": endpoint},
                **fixture,
            }

        # ---- Live: authenticated proxy to the provider's own REST API via Nango ----
        url = f"{settings.nango_host.rstrip('/')}/proxy{endpoint}"
        headers = {
            "Authorization": f"Bearer {secret}",
            "Provider-Config-Key": self.provider,
            "Connection-Id": connection_id,
        }
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.request(
                    method, url, headers=headers, params=query, json=body
                )
                resp.raise_for_status()
                data = resp.json() if resp.content else {}
        except httpx.HTTPError as exc:
            return {"status": "error", "provider": self.provider, "error": str(exc)}
        return {"status": "ok", "provider": self.provider, "data": data}


def _gmail_sandbox(method: str, endpoint: str, args: dict) -> dict:
    """Gmail-shaped demo fixtures (List/Get Message, Send)."""
    if method == "POST" and "send" in endpoint:
        return {"id": "sandbox-sent-1", "labelIds": ["SENT"]}
    if endpoint.startswith("/messages"):
        return {
            "id": endpoint.rsplit("/", 1)[-1] or "sandbox-msg-1",
            "from": "billing@acme-supplies.example",
            "subject": "Invoice INV-1042",
            "attachments": [{"filename": "invoice-INV-1042.pdf", "attachment_id": "att_1"}],
        }
    return {}


def _quickbooks_sandbox(method: str, endpoint: str, args: dict) -> dict:
    """QuickBooks-shaped demo fixtures (Vendors, Bills)."""
    if endpoint.startswith("/vendor"):
        return {"id": "V-1001", "vendors": [{"Id": "V-1001", "DisplayName": "Acme Supplies"}]}
    if endpoint.startswith("/bill") and method == "GET":
        return {"bills": []}  # none found => not a duplicate
    if endpoint.startswith("/bill") and method == "POST":
        return {"id": "BILL-5001", "status": "created"}
    return {}


def _sheets_sandbox(method: str, endpoint: str, args: dict) -> dict:
    """Google Sheets-shaped demo fixtures (read rows for dup-check; append a row)."""
    if method == "GET":  # read existing invoice rows (duplicate-check source in demo mode)
        return {"values": []}  # empty => not a duplicate
    if method == "POST":  # append the invoice metadata row
        return {"updates": {"updatedRange": "Invoices!A2:F2", "updatedRows": 1}, "saved": True}
    return {}


def _drive_sandbox(method: str, endpoint: str, args: dict) -> dict:
    """Google Drive-shaped demo fixtures (upload/archive a file)."""
    if method == "POST":
        return {
            "id": "drive-file-9001",
            "name": "invoice-INV-1042.pdf",
            "webViewLink": "https://drive.example/sandbox/INV-1042",
        }
    return {}


def nango_connectors() -> list[NangoConnector]:
    # Keys/provider match the tenant's Nango integration IDs (google-mail / google-sheet /
    # google-drive). QuickBooks is registered for the future "accounting connector" branch.
    return [
        NangoConnector("google-mail", "Gmail (via Nango)", _gmail_sandbox),
        NangoConnector("google-sheet", "Google Sheets (via Nango)", _sheets_sandbox),
        NangoConnector("google-drive", "Google Drive (via Nango)", _drive_sandbox),
        NangoConnector("quickbooks", "QuickBooks Online (via Nango)", _quickbooks_sandbox),
    ]
