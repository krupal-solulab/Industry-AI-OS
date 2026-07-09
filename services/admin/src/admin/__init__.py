"""Admin API — control-plane operations for a tenant.

Manages the tenant registry, aggregates system health, and proxies user/role,
connector, and audit reads (forwarding the signed tenant context) so operators have
one console. User/connector mutations remain owned by their services; admin is a
convenience facade, not a second source of truth.
"""
