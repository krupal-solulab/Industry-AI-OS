"""workflow pack framework — packs, definitions, runs, step runs, approval tasks

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-09

Milestone 2. All tables are tenant-scoped with RLS. Definitions are stored as jsonb
(seeded from the repo pack files); runs/step_runs/approval_tasks capture execution.
"""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

TENANT_TABLES = [
    "workflow_packs",
    "workflow_definitions",
    "workflow_runs",
    "workflow_step_runs",
    "approval_tasks",
]


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
        CREATE TABLE IF NOT EXISTS workflow_packs (
            id         uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
            tenant_id  text NOT NULL,
            pack_key   text NOT NULL,
            industry   text NOT NULL,
            version    text NOT NULL DEFAULT '1.0.0',
            enabled    boolean NOT NULL DEFAULT true,
            manifest   jsonb NOT NULL DEFAULT '{}'::jsonb,
            created_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE UNIQUE INDEX IF NOT EXISTS ux_packs_tenant_key
            ON workflow_packs(tenant_id, pack_key);
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS workflow_definitions (
            id           uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
            tenant_id    text NOT NULL,
            pack_key     text NOT NULL,
            workflow_key text NOT NULL,
            version      text NOT NULL DEFAULT '1.0.0',
            definition   jsonb NOT NULL,
            created_at   timestamptz NOT NULL DEFAULT now()
        );
        CREATE UNIQUE INDEX IF NOT EXISTS ux_defs_tenant_pack_wf
            ON workflow_definitions(tenant_id, pack_key, workflow_key);
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS workflow_runs (
            id           uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
            tenant_id    text NOT NULL,
            run_id       text NOT NULL,
            pack_key     text NOT NULL,
            workflow_key text NOT NULL,
            status       text NOT NULL DEFAULT 'running',
            context      jsonb NOT NULL DEFAULT '{}'::jsonb,
            current_step text,
            created_by   text,
            created_at   timestamptz NOT NULL DEFAULT now(),
            updated_at   timestamptz NOT NULL DEFAULT now()
        );
        CREATE UNIQUE INDEX IF NOT EXISTS ux_runs_run_id ON workflow_runs(run_id);
        CREATE INDEX IF NOT EXISTS ix_runs_tenant ON workflow_runs(tenant_id);
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS workflow_step_runs (
            id         uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
            tenant_id  text NOT NULL,
            run_id     text NOT NULL,
            step_id    text NOT NULL,
            type       text NOT NULL,
            status     text NOT NULL DEFAULT 'pending',
            input      jsonb,
            output     jsonb,
            error      text,
            started_at timestamptz,
            ended_at   timestamptz
        );
        CREATE INDEX IF NOT EXISTS ix_step_runs_run ON workflow_step_runs(run_id);
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS approval_tasks (
            id            uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
            tenant_id     text NOT NULL,
            run_id        text NOT NULL,
            step_id       text NOT NULL,
            approver_role text,
            status        text NOT NULL DEFAULT 'pending',
            decision      text,
            decided_by    text,
            comment       text,
            created_at    timestamptz NOT NULL DEFAULT now(),
            decided_at    timestamptz
        );
        CREATE INDEX IF NOT EXISTS ix_approvals_tenant_status
            ON approval_tasks(tenant_id, status);
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
