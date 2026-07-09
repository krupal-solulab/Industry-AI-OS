"""API Gateway — the single public entry point.

Responsibilities, in order, for every request:
  1. Authenticate the Keycloak JWT (validate signature + issuer).
  2. Resolve the TenantContext (user, tenant = Keycloak Organization, roles).
  3. Rate-limit per tenant+user.
  4. Mint an HMAC-signed internal context header and route to the target service.

Downstream services trust ONLY this signed header — never a client-supplied tenant
id. The gateway exposes both REST (reverse proxy) and GraphQL.
"""
