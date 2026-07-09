-- Bootstrap all databases the stack needs on the single Postgres instance.
-- Runs once on a fresh data volume (docker-entrypoint-initdb.d).
-- The app database `aios` is created by POSTGRES_DB; here we add the rest and
-- enable pgvector where the app stores embeddings.

CREATE DATABASE keycloak;
CREATE DATABASE temporal;
CREATE DATABASE temporal_visibility;
CREATE DATABASE langfuse;

-- pgvector for the knowledge layer (RAG embeddings) in the app DB.
\connect aios
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- App runtime role is intentionally NON-superuser and lacks BYPASSRLS, so a
-- forgotten WHERE tenant_id cannot leak rows — RLS still filters. Migrations run
-- as the owner (POSTGRES_USER); the app connects as this role in real envs.
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'aios_app') THEN
        CREATE ROLE aios_app LOGIN PASSWORD 'aios_app' NOSUPERUSER NOBYPASSRLS;
    END IF;
END
$$;
GRANT CONNECT ON DATABASE aios TO aios_app;
