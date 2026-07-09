"""initial platform schema — control plane + tenant-scoped tables with RLS

Revision ID: 0001
Revises:
Create Date: 2026-07-08

Industry-neutral by design: `documents`, `workflow_instances`, `connectors`, etc.
carry no industry semantics. Every tenant-owned table gets RLS enforced.
"""
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

# Tenant-owned tables that must be isolated by RLS.
TENANT_TABLES = [
    "documents",
    "document_chunks",
    "chat_sessions",
    "chat_messages",
    "workflow_instances",
    "connectors",
    "audit_log",
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
    op.execute('CREATE EXTENSION IF NOT EXISTS vector;')
    op.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp";')

    # ---- control plane: tenant registry (NOT tenant-scoped) ----------------
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS tenants (
            id              uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
            slug            text UNIQUE NOT NULL,
            name            text NOT NULL,
            keycloak_org_id text,
            status          text NOT NULL DEFAULT 'active',
            settings        jsonb NOT NULL DEFAULT '{}'::jsonb,
            created_at      timestamptz NOT NULL DEFAULT now()
        );
        """
    )

    # ---- documents ----------------------------------------------------------
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS documents (
            id           uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
            tenant_id    text NOT NULL,
            filename     text NOT NULL,
            content_type text,
            object_key   text NOT NULL,
            status       text NOT NULL DEFAULT 'uploaded',
            size_bytes   bigint,
            created_by   text,
            meta         jsonb NOT NULL DEFAULT '{}'::jsonb,
            created_at   timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX IF NOT EXISTS ix_documents_tenant ON documents(tenant_id);
        """
    )

    # ---- document chunks (RAG embeddings) ----------------------------------
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS document_chunks (
            id          uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
            tenant_id   text NOT NULL,
            document_id uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            chunk_index int NOT NULL,
            content     text NOT NULL,
            embedding   vector(1536),
            created_at  timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX IF NOT EXISTS ix_chunks_tenant ON document_chunks(tenant_id);
        CREATE INDEX IF NOT EXISTS ix_chunks_doc ON document_chunks(document_id);
        """
    )

    # ---- chat sessions + messages ------------------------------------------
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS chat_sessions (
            id         uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
            tenant_id  text NOT NULL,
            user_id    text NOT NULL,
            title      text,
            model      text,
            created_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX IF NOT EXISTS ix_sessions_tenant ON chat_sessions(tenant_id);

        CREATE TABLE IF NOT EXISTS chat_messages (
            id         uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
            tenant_id  text NOT NULL,
            session_id uuid NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
            role       text NOT NULL,
            content    text NOT NULL,
            tokens     int,
            created_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX IF NOT EXISTS ix_messages_session ON chat_messages(session_id);
        """
    )

    # ---- workflow instances (generic approval flow) ------------------------
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS workflow_instances (
            id          uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
            tenant_id   text NOT NULL,
            workflow_id text NOT NULL,
            run_id      text,
            type        text NOT NULL DEFAULT 'document_review_approval',
            status      text NOT NULL DEFAULT 'running',
            document_id uuid,
            summary     text,
            decision    text,
            decided_by  text,
            comment     text,
            created_by  text,
            created_at  timestamptz NOT NULL DEFAULT now(),
            updated_at  timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX IF NOT EXISTS ix_wf_tenant ON workflow_instances(tenant_id);
        CREATE UNIQUE INDEX IF NOT EXISTS ux_wf_workflow_id ON workflow_instances(workflow_id);
        """
    )

    # ---- connectors (Connector Hub registry) -------------------------------
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS connectors (
            id         uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
            tenant_id  text NOT NULL,
            key        text NOT NULL,
            name       text NOT NULL,
            kind       text NOT NULL,
            enabled    boolean NOT NULL DEFAULT true,
            config     jsonb NOT NULL DEFAULT '{}'::jsonb,
            created_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE UNIQUE INDEX IF NOT EXISTS ux_connectors_tenant_key
            ON connectors(tenant_id, key);
        """
    )

    # ---- audit log (append-only) -------------------------------------------
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS audit_log (
            id            uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
            tenant_id     text NOT NULL,
            actor_id      text NOT NULL,
            actor_email   text,
            action        text NOT NULL,
            resource_kind text NOT NULL,
            resource_id   text NOT NULL,
            before        jsonb,
            after         jsonb,
            metadata      jsonb NOT NULL DEFAULT '{}'::jsonb,
            request_id    text,
            created_at    timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX IF NOT EXISTS ix_audit_tenant_time
            ON audit_log(tenant_id, created_at DESC);
        """
    )

    # ---- append-only guard for the audit log -------------------------------
    # Reject UPDATE/DELETE at the database level; the audit log is write-once.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION audit_log_immutable() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'audit_log is append-only; % is not permitted', TG_OP;
        END;
        $$ LANGUAGE plpgsql;

        DROP TRIGGER IF EXISTS trg_audit_immutable ON audit_log;
        CREATE TRIGGER trg_audit_immutable
            BEFORE UPDATE OR DELETE ON audit_log
            FOR EACH ROW EXECUTE FUNCTION audit_log_immutable();
        """
    )

    # ---- enforce RLS on every tenant-owned table ---------------------------
    for table in TENANT_TABLES:
        op.execute(_rls(table))

    # ---- grant the non-superuser app role least-privilege DML --------------
    op.execute(
        """
        GRANT USAGE ON SCHEMA public TO aios_app;
        GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO aios_app;
        GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO aios_app;
        ALTER DEFAULT PRIVILEGES IN SCHEMA public
            GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO aios_app;
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_audit_immutable ON audit_log;")
    op.execute("DROP FUNCTION IF EXISTS audit_log_immutable();")
    for table in [
        "audit_log",
        "connectors",
        "workflow_instances",
        "chat_messages",
        "chat_sessions",
        "document_chunks",
        "documents",
        "tenants",
    ]:
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE;")
