"""user profiles — role + login_source, keyed to the Keycloak identity

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-10

Keycloak remains the credential store / token issuer (ADR-0001) — this table only
carries platform-specific profile fields per user: their business role and the
industry vertical (login_source) they signed up under. Tenant-scoped with RLS,
same pattern as every other table in this schema.
"""
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

TENANT_TABLES = ["user_profiles"]


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
        CREATE TABLE IF NOT EXISTS user_profiles (
            id                uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
            tenant_id         text NOT NULL,
            keycloak_user_id  text NOT NULL,
            email             text NOT NULL,
            first_name        text,
            last_name         text,
            role              text NOT NULL DEFAULT 'member'
                                  CHECK (role IN ('owner', 'admin', 'member', 'viewer')),
            login_source      text
                                  CHECK (login_source IN
                                      ('accounting', 'legal', 'litigation', 'construction')),
            created_at        timestamptz NOT NULL DEFAULT now()
        );
        CREATE UNIQUE INDEX IF NOT EXISTS ux_user_profiles_kc_user
            ON user_profiles(keycloak_user_id);
        CREATE INDEX IF NOT EXISTS ix_user_profiles_tenant ON user_profiles(tenant_id);
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
    for table in reversed(TENANT_TABLES):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE;")
