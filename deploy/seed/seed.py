"""Seed the control-plane registry with the demo tenant.

Keycloak already imports the demo Organization + four users (owner/admin/member/
viewer) via realm-export.json. This script maps that Organization to a row in the
`tenants` control-plane table (with a per-tenant default chat model), so the platform
has a registered tenant to work with. Idempotent.
"""

from __future__ import annotations

import json
import os
import time

import httpx
from sqlalchemy import create_engine, text

DB_URL = os.environ.get(
    "DATABASE_URL_SYNC", "postgresql+psycopg://aios:aios@postgres:5432/aios"
)
KEYCLOAK_URL = os.environ.get("KEYCLOAK_URL", "http://keycloak:8080")
REALM = os.environ.get("KEYCLOAK_REALM", "industry-ai-os")
KC_ADMIN = os.environ.get("KEYCLOAK_ADMIN", "admin")
KC_PASSWORD = os.environ.get("KEYCLOAK_ADMIN_PASSWORD", "admin")

DEMO_SLUG = "demo"
DEMO_NAME = "Demo Tenant"

# The demo tenant's connector allowlist (opt-in entitlements, ADR-0019). Every non-
# reference connector in the registry — grants the demo tenant (which all public signups
# share, D10) access so the Connector Hub + workflow-builder palette aren't empty. `echo`
# is a reference connector and is always usable, so it's omitted.
DEFAULT_CONNECTORS = [
    "nango.google-mail",
    "nango.google-sheet",
    "nango.google-drive",
    "nango.quickbooks",
    "microsoft-graph",
    "composio",
]


def _keycloak_org_id() -> str | None:
    """Best-effort: resolve the demo Organization's id from Keycloak."""
    for attempt in range(10):
        try:
            with httpx.Client(timeout=10) as c:
                tok = c.post(
                    f"{KEYCLOAK_URL}/realms/master/protocol/openid-connect/token",
                    data={
                        "grant_type": "password",
                        "client_id": "admin-cli",
                        "username": KC_ADMIN,
                        "password": KC_PASSWORD,
                    },
                ).json()["access_token"]
                orgs = c.get(
                    f"{KEYCLOAK_URL}/admin/realms/{REALM}/organizations",
                    headers={"Authorization": f"Bearer {tok}"},
                ).json()
            for org in orgs:
                if org.get("alias") == DEMO_SLUG or org.get("name") in (DEMO_SLUG, DEMO_NAME):
                    return org["id"]
            return None
        except Exception as exc:  # Keycloak may still be importing the realm
            print(f"[seed] keycloak not ready ({exc}); retry {attempt + 1}/10")
            time.sleep(5)
    return None


def _grant_default_entitlements(engine, tenant_id: str) -> None:
    """Grandfather the demo tenant onto the default connector allowlist (opt-in
    entitlements, ADR-0019). Sets `app.tenant_id` via set_config so the RLS WITH CHECK on
    connector_entitlements passes (the table FORCEs row-level security). Idempotent."""
    with engine.begin() as conn:
        conn.execute(text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": tenant_id})
        for key in DEFAULT_CONNECTORS:
            conn.execute(
                text(
                    """INSERT INTO connector_entitlements
                           (tenant_id, connector_key, allowed, created_by)
                       VALUES (:tid, :key, true, 'seed')
                       ON CONFLICT (tenant_id, connector_key)
                       DO UPDATE SET allowed = true, updated_at = now()"""
                ),
                {"tid": tenant_id, "key": key},
            )
    print(f"[seed] granted {len(DEFAULT_CONNECTORS)} connector entitlements to '{tenant_id}'")


def main() -> None:
    org_id = _keycloak_org_id()
    print(f"[seed] demo organization id: {org_id}")
    engine = create_engine(DB_URL, future=True)
    settings = {"chat_model": os.environ.get("DEFAULT_CHAT_MODEL", "claude-primary")}
    with engine.begin() as conn:
        conn.execute(
            text(
                """INSERT INTO tenants (slug, name, keycloak_org_id, settings)
                   VALUES (:slug, :name, :org, CAST(:settings AS jsonb))
                   ON CONFLICT (slug) DO UPDATE
                     SET name = :name,
                         keycloak_org_id = COALESCE(:org, tenants.keycloak_org_id),
                         settings = CAST(:settings AS jsonb)"""
            ),
            {
                "slug": DEMO_SLUG, "name": DEMO_NAME, "org": org_id,
                "settings": json.dumps(settings),
            },
        )
    print(f"[seed] tenant '{DEMO_SLUG}' registered with settings {settings}")

    if org_id:
        _grant_default_entitlements(engine, org_id)
    else:
        print("[seed] no org id — skipped connector entitlements")

    print("[seed] demo users (password 'Passw0rd!'):")
    for role in ("owner", "admin", "member", "viewer"):
        print(f"        {role}@demo.aios.local  [{role}]")


if __name__ == "__main__":
    main()
