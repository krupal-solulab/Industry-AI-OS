"""Industry AI OS — shared platform spine.

Every heavy external dependency (Keycloak, Cerbos, LiteLLM, Postgres, OTel) sits
behind an interface in this package so services can be swapped and tools replaced
without touching callers. If a service imports a third-party enterprise client
directly, it belongs here instead.
"""

from ai_os_shared.errors import (
    AuthenticationError,
    AuthorizationError,
    NotFoundError,
    PlatformError,
    TenantContextError,
)
from ai_os_shared.settings import Settings, get_settings
from ai_os_shared.tenant_context import (
    TenantContext,
    current_context,
    require_context,
    set_context,
)

__all__ = [
    "Settings",
    "get_settings",
    "TenantContext",
    "current_context",
    "require_context",
    "set_context",
    "PlatformError",
    "AuthenticationError",
    "AuthorizationError",
    "NotFoundError",
    "TenantContextError",
]

__version__ = "0.1.0"
