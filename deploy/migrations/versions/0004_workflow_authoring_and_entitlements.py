"""workflow authoring + connector entitlements

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-14

Two additions for the user-authored workflow builder (see ADR-0019):

1. `workflow_definitions` gains provenance columns (`source`, `created_by`, `updated_at`)
   so user-built flows live in the same table as seeded pack flows and the runtime can
   tell them apart. `source` is 'seed' for repo-shipped packs and 'user' for flows built
   in the visual builder. User flows use the reserved pack_key 'custom'.

2. A new `connector_entitlements` table: the per-tenant allowlist of which connectors a
   tenant may see/enable/use. Opt-in — a connector is entitled only when a row exists with
   allowed = true (the `echo` reference connector is always usable). Existing tenants are
   grandfathered via the app endpoint POST /connectors/entitlements/grant-defaults (data
   can't be seeded here: these tables FORCE row-level security and a migration has no
   tenant context, so any INSERT would fail the RLS WITH CHECK).

Tenant-scoped with RLS, same pattern as every other table in this schema.
"""
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

TENANT_TABLES = ["connector_entitlements"]


def _rls(table: str) -> str:
    return f"""
    ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;
    ALTER TABLE {table} FORCE ROW LEVEL SECURITY;
    DROP POLICY IF EXISTS tenant_isolation ON {table};
    CREATE POLICY tenant_isolation ON {table}
        USING (tenant_id = current_setting('app.tenant_id', true))
        WITH CHECK (tenant_id = current_setting('app.tenant_id', true));
    """


def upgrade() -> None:
    # 1. Provenance on workflow_definitions.
    op.execute(
        """
        ALTER TABLE workflow_definitions
            ADD COLUMN IF NOT EXISTS source     text NOT NULL DEFAULT 'seed',
            ADD COLUMN IF NOT EXISTS created_by text,
            ADD COLUMN IF NOT EXISTS updated_at timestamptz NOT NULL DEFAULT now();
        CREATE INDEX IF NOT EXISTS ix_defs_tenant_source
            ON workflow_definitions(tenant_id, source);
        """
    )

    # 2. Per-tenant connector allowlist.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS connector_entitlements (
            id            uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
            tenant_id     text NOT NULL,
            connector_key text NOT NULL,
            allowed       boolean NOT NULL DEFAULT true,
            created_by    text,
            created_at    timestamptz NOT NULL DEFAULT now(),
            updated_at    timestamptz NOT NULL DEFAULT now()
        );
        CREATE UNIQUE INDEX IF NOT EXISTS ux_entitlements_tenant_connector
            ON connector_entitlements(tenant_id, connector_key);
        """
    )

    for table in TENANT_TABLES:
        op.execute(_rls(table))

    op.execute(
        """
        GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO aios_app;
        GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO aios_app;
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS connector_entitlements CASCADE;")
    op.execute(
        """
        ALTER TABLE workflow_definitions
            DROP COLUMN IF EXISTS source,
            DROP COLUMN IF EXISTS created_by,
            DROP COLUMN IF EXISTS updated_at;
        """
    )
