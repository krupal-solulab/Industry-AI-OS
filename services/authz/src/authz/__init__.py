"""Authorization service — façade over Cerbos.

Enforcement is decentralized: every service calls `ai_os_shared.authz.check()`
in-process (one network hop to Cerbos). This service exists so the gateway, admin
tooling, and non-Python callers can evaluate a decision, and to surface the starter
role model. Swapping Cerbos for OPA is isolated to `ai_os_shared.authz`.
"""
