"""Centralized, env-driven configuration shared by every service.

One Settings object, populated from environment variables (documented in
`.env.example`). Services never read os.environ directly — they call
`get_settings()` so config resolution stays uniform and testable.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False
    )

    # -- deployment ----------------------------------------------------------
    deploy_mode: str = Field("on-prem", alias="DEPLOY_MODE")
    environment: str = Field("development", alias="ENVIRONMENT")
    log_level: str = Field("info", alias="LOG_LEVEL")
    service_name: str = Field("aios-service", alias="SERVICE_NAME")

    # -- AI Assistant workspace-awareness mode -------------------------------
    # strict:          reject questions unrelated to the active workspace.
    # strict_lenient:  (default) answer anything, but append a workspace reminder.
    # lenient:         behave like a normal assistant, no reminder.
    # Change behavior by setting ASSISTANT_MODE only — never by editing logic.
    assistant_mode: str = Field("strict_lenient", alias="ASSISTANT_MODE")

    # -- postgres ------------------------------------------------------------
    database_url: str = Field(
        "postgresql+asyncpg://aios:aios@localhost:5432/aios", alias="DATABASE_URL"
    )
    database_url_sync: str = Field(
        "postgresql+psycopg://aios:aios@localhost:5432/aios", alias="DATABASE_URL_SYNC"
    )

    # -- keycloak ------------------------------------------------------------
    keycloak_url: str = Field("http://localhost:8080", alias="KEYCLOAK_URL")
    keycloak_realm: str = Field("industry-ai-os", alias="KEYCLOAK_REALM")
    keycloak_client_id: str = Field("aios-gateway", alias="KEYCLOAK_CLIENT_ID")
    keycloak_client_secret: str = Field("", alias="KEYCLOAK_CLIENT_SECRET")
    keycloak_admin: str = Field("admin", alias="KEYCLOAK_ADMIN")
    keycloak_admin_password: str = Field("admin", alias="KEYCLOAK_ADMIN_PASSWORD")
    keycloak_issuer: str = Field(
        "http://localhost:8080/realms/industry-ai-os", alias="KEYCLOAK_ISSUER"
    )

    # -- cerbos --------------------------------------------------------------
    cerbos_url: str = Field("http://localhost:3592", alias="CERBOS_URL")

    # -- minio ---------------------------------------------------------------
    minio_endpoint: str = Field("localhost:9000", alias="MINIO_ENDPOINT")
    minio_access_key: str = Field("aios", alias="MINIO_ACCESS_KEY")
    minio_secret_key: str = Field("aios", alias="MINIO_SECRET_KEY")
    minio_secure: bool = Field(False, alias="MINIO_SECURE")
    minio_bucket: str = Field("aios-documents", alias="MINIO_BUCKET")

    # -- redis / nats --------------------------------------------------------
    redis_url: str = Field("redis://localhost:6379/0", alias="REDIS_URL")
    nats_url: str = Field("nats://localhost:4222", alias="NATS_URL")

    # -- Nango (connector auth broker: OAuth + token refresh + REST proxy) ---
    # Empty secret => connectors run in SANDBOX (provider-shaped fixtures). Set the
    # secret + a per-tenant connection id to hit live provider APIs via Nango's proxy.
    nango_secret_key: str = Field("", alias="NANGO_SECRET_KEY")
    nango_host: str = Field("https://api.nango.dev", alias="NANGO_HOST")

    # -- temporal ------------------------------------------------------------
    temporal_host: str = Field("localhost:7233", alias="TEMPORAL_HOST")
    temporal_namespace: str = Field("default", alias="TEMPORAL_NAMESPACE")
    temporal_task_queue: str = Field("aios-workflows", alias="TEMPORAL_TASK_QUEUE")

    # -- litellm / llm -------------------------------------------------------
    litellm_url: str = Field("http://localhost:4000", alias="LITELLM_URL")
    litellm_master_key: str = Field("sk-aios-master", alias="LITELLM_MASTER_KEY")
    anthropic_api_key: str = Field("", alias="ANTHROPIC_API_KEY")
    openai_api_key: str = Field("", alias="OPENAI_API_KEY")
    default_chat_model: str = Field("claude-primary", alias="DEFAULT_CHAT_MODEL")
    default_embed_model: str = Field("text-embedding-3-small", alias="DEFAULT_EMBED_MODEL")

    # -- langfuse ------------------------------------------------------------
    langfuse_host: str = Field("http://localhost:3000", alias="LANGFUSE_HOST")
    langfuse_public_key: str = Field("", alias="LANGFUSE_PUBLIC_KEY")
    langfuse_secret_key: str = Field("", alias="LANGFUSE_SECRET_KEY")

    # -- otel ----------------------------------------------------------------
    otel_endpoint: str = Field("http://localhost:4317", alias="OTEL_EXPORTER_OTLP_ENDPOINT")
    otel_namespace: str = Field("aios", alias="OTEL_SERVICE_NAMESPACE")

    # -- internal trust ------------------------------------------------------
    internal_context_secret: str = Field(
        "change-me-internal-shared-secret", alias="INTERNAL_CONTEXT_SECRET"
    )

    # -- downstream service URLs (used by the gateway) -----------------------
    identity_url: str = Field("http://localhost:8001", alias="IDENTITY_URL")
    authz_url: str = Field("http://localhost:8002", alias="AUTHZ_URL")
    orchestrator_url: str = Field("http://localhost:8003", alias="ORCHESTRATOR_URL")
    knowledge_url: str = Field("http://localhost:8004", alias="KNOWLEDGE_URL")
    workflows_url: str = Field("http://localhost:8005", alias="WORKFLOWS_URL")
    connectors_url: str = Field("http://localhost:8006", alias="CONNECTORS_URL")
    audit_url: str = Field("http://localhost:8007", alias="AUDIT_URL")
    admin_url: str = Field("http://localhost:8008", alias="ADMIN_URL")

    # -- rate limiting -------------------------------------------------------
    rate_limit_per_minute: int = Field(120, alias="RATE_LIMIT_PER_MINUTE")

    # -- CORS (browser frontends that call the gateway) ----------------------
    # Comma-separated allowed origins. Defaults cover the Vite/TanStack dev ports.
    cors_origins: str = Field(
        "http://localhost:3000,http://localhost:5173,http://localhost:8080",
        alias="CORS_ORIGINS",
    )

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        return self.environment.lower() in {"production", "prod"}


@lru_cache
def get_settings() -> Settings:
    """Process-wide singleton. `lru_cache` makes it cheap and consistent."""
    return Settings()
