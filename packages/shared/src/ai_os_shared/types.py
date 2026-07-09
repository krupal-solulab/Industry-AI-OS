"""Shared domain types used across service boundaries.

Kept deliberately industry-neutral. If a type here starts to look like it belongs
to insurance/construction/etc., it belongs in a later industry pack, not the core.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class Role(str, Enum):
    """Starter platform roles. Industry packs may add roles later."""

    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"
    VIEWER = "viewer"


class Principal(BaseModel):
    """The acting user, as understood by the authorization layer (Cerbos)."""

    id: str
    tenant_id: str
    roles: list[Role] = Field(default_factory=list)
    email: str | None = None
    attributes: dict[str, str] = Field(default_factory=dict)


class Resource(BaseModel):
    """A thing an action is performed on, for authorization checks."""

    kind: str  # e.g. "document", "workflow", "connector", "tenant"
    id: str
    tenant_id: str
    attributes: dict[str, str] = Field(default_factory=dict)


class HealthStatus(str, Enum):
    OK = "ok"
    DEGRADED = "degraded"
    DOWN = "down"


class HealthReport(BaseModel):
    service: str
    status: HealthStatus
    version: str = "0.1.0"
    checks: dict[str, str] = Field(default_factory=dict)
