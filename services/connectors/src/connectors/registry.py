"""In-process registry of available connectors.

The catalog of connector *implementations* is global; whether one is *enabled* and
its *config* are per-tenant and live in the `connectors` table (RLS-scoped).
"""

from __future__ import annotations

from connectors.base import (
    ComposioConnector,
    Connector,
    EchoConnector,
    MicrosoftGraphConnector,
)

_REGISTRY: dict[str, Connector] = {
    c.key: c
    for c in (EchoConnector(), MicrosoftGraphConnector(), ComposioConnector())
}


def all_connectors() -> list[Connector]:
    return list(_REGISTRY.values())


def get_connector(key: str) -> Connector | None:
    return _REGISTRY.get(key)
