"""Connector interface + built-in connectors.

Every connector — whether backed by an MCP server, Composio, or a direct client —
implements the same `Connector` interface: advertise `tools`, and `invoke(tool,
args)`. Callers never learn which backend a connector uses.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field

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
