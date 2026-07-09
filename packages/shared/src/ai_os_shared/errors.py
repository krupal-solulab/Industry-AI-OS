"""Platform error taxonomy.

Services raise these; the shared FastAPI app factory (`app.py`) maps them to
consistent HTTP responses so every service speaks the same error dialect.
"""

from __future__ import annotations


class PlatformError(Exception):
    """Base for all platform errors. Carries an HTTP status and a stable code."""

    status_code: int = 500
    code: str = "platform_error"

    def __init__(self, message: str, *, detail: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail or {}


class AuthenticationError(PlatformError):
    status_code = 401
    code = "authentication_error"


class AuthorizationError(PlatformError):
    status_code = 403
    code = "authorization_error"


class TenantContextError(PlatformError):
    """Raised when tenant context is missing, unsigned, or tampered with."""

    status_code = 400
    code = "tenant_context_error"


class NotFoundError(PlatformError):
    status_code = 404
    code = "not_found"


class ValidationError(PlatformError):
    status_code = 422
    code = "validation_error"


class UpstreamError(PlatformError):
    """A dependency (Keycloak, Cerbos, LiteLLM, a connector) failed."""

    status_code = 502
    code = "upstream_error"


class RateLimitError(PlatformError):
    status_code = 429
    code = "rate_limited"
