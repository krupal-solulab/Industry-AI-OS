"""connector access requests — request → approve flow for the Connector Hub

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-15

Backs the enterprise "Pending Permissions" section (ADR-0019): a member of a *restricted*
tenant can request access to a connector it isn't entitled to; an owner/admin approves
(which grants the entitlement) or rejects. Tenant-scoped with RLS. A partial unique index
allows at most one *pending* request per (tenant, connector) while keeping history of
approved/rejected ones.
"""
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

TENANT_TABLES = ["connector_access_requests"]


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
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS connector_access_requests (
            id            uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
            tenant_id     text NOT NULL,
            connector_key text NOT NULL,
            status        text NOT NULL DEFAULT 'pending'
                              CHECK (status IN ('pending', 'approved', 'rejected')),
            requested_by  text,
            note          text,
            decided_by    text,
            created_at    timestamptz NOT NULL DEFAULT now(),
            decided_at    timestamptz
        );
        CREATE UNIQUE INDEX IF NOT EXISTS ux_access_requests_pending
            ON connector_access_requests(tenant_id, connector_key)
            WHERE status = 'pending';
        CREATE INDEX IF NOT EXISTS ix_access_requests_tenant_status
            ON connector_access_requests(tenant_id, status);
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
    op.execute("DROP TABLE IF EXISTS connector_access_requests CASCADE;")
