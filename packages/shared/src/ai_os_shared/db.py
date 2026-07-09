"""Database access — async SQLAlchemy engine + tenant-scoped RLS session.

Every tenant-owned table inherits `TenantMixin` (a `tenant_id` column). Every
session opened via `tenant_session()` runs `SET LOCAL app.tenant_id = <tenant>`,
so PostgreSQL Row-Level Security filters rows even if application code forgets a
`WHERE tenant_id = …`. The DB — not the app — is the last line of defense.

This is the ONLY place engines/sessions are created, so promoting a tenant to
schema- or database-level isolation later changes this module and nothing else.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import String, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from ai_os_shared.settings import get_settings
from ai_os_shared.tenant_context import TenantContext, require_context

_engine = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def get_engine():
    global _engine, _sessionmaker
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(
            settings.database_url,
            pool_pre_ping=True,
            pool_size=10,
            max_overflow=20,
            echo=False,
        )
        _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)
    return _engine


def _maker() -> async_sessionmaker[AsyncSession]:
    if _sessionmaker is None:
        get_engine()
    assert _sessionmaker is not None
    return _sessionmaker


class Base(DeclarativeBase):
    """Declarative base for all platform models."""


class TenantMixin:
    """Adds the tenant discriminator every tenant-owned table must carry."""

    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)


@asynccontextmanager
async def tenant_session(ctx: TenantContext | None = None) -> AsyncIterator[AsyncSession]:
    """Open a session pinned to a tenant via RLS.

    `SET LOCAL` scopes the setting to the surrounding transaction, so it cannot
    leak to another request reusing the pooled connection.
    """
    ctx = ctx or require_context()
    session = _maker()()
    try:
        # Parameterized via a literal-safe cast; tenant ids are validated UUIDs/slugs.
        await session.execute(
            text("SELECT set_config('app.tenant_id', :tid, true)"),
            {"tid": str(ctx.tenant_id)},
        )
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


@asynccontextmanager
async def admin_session() -> AsyncIterator[AsyncSession]:
    """Unscoped session for platform-level tables (tenants registry, etc.).

    Use ONLY for genuinely cross-tenant control-plane data. Never for tenant data.
    """
    session = _maker()()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


def new_uuid() -> str:
    return str(uuid.uuid4())


RLS_POLICY_SQL = """
ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;
ALTER TABLE {table} FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON {table};
CREATE POLICY tenant_isolation ON {table}
    USING (tenant_id = current_setting('app.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true));
"""


def rls_policy(table: str) -> str:
    """Return the SQL that enables + enforces RLS on a tenant-owned table.

    Called from Alembic migrations so isolation lives in version control, not code.
    """
    return RLS_POLICY_SQL.format(table=table)
