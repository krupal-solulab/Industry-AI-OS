"""Identity service — the platform's bridge to Keycloak.

Keycloak owns authentication and the user directory. This service exposes a
tenant-scoped view (Organizations = tenants) and admin operations (create user,
assign role) via the Keycloak Admin API. No other service talks to Keycloak's
admin surface directly.
"""
