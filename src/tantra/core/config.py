"""
Tantra AI — Centralised configuration
All settings loaded from environment variables / .env file.
"""
from __future__ import annotations

from enum import Enum
from functools import lru_cache
from typing import Optional

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(str, Enum):
    development = "development"
    staging = "staging"
    production = "production"


class ModelTier(str, Enum):
    """LiteLLM model aliases — maps to actual models in litellm_config.yaml"""
    frontier = "frontier"   # Claude 3.5 Sonnet / GPT-4o  (strategic)
    director = "director"   # Llama 3.3 70B local         (dept planning)
    manager  = "manager"    # Qwen 2.5 72B local          (execution)
    worker   = "worker"     # Phi-4 14B local             (focused tasks)
    coder    = "coder"      # DeepSeek Coder V2 16B       (code)
    fast     = "fast"       # Mistral Nemo 12B            (routing/classify)
    embedder = "embedder"   # nomic-embed-text            (RAG)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── App ──────────────────────────────────────────────────────────────────
    environment: Environment = Environment.development
    debug: bool = False
    secret_key: SecretStr = Field(default="change-me-please-use-a-long-random-string")
    timezone: str = "Asia/Kolkata"
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    api_base_url: str = "http://localhost:8000"   # Used in OAuth redirect URIs
    cors_origins: list[str] = ["http://localhost:3000", "http://localhost:8000",
                                "http://localhost:5678", "http://localhost:4000"]

    # ── Database ─────────────────────────────────────────────────────────────
    database_url: str = "postgresql+asyncpg://tantra:tantra_secret@postgres:5432/tantra"
    database_sync_url: str = "postgresql://tantra:tantra_secret@postgres:5432/tantra"
    db_pool_size: int = 10
    db_max_overflow: int = 20

    # ── Redis ─────────────────────────────────────────────────────────────────
    redis_url: str = "redis://:tantra_redis_secret@localhost:6379/0"
    celery_broker_url: str = "redis://:tantra_redis_secret@localhost:6379/1"
    celery_result_backend: str = "redis://:tantra_redis_secret@localhost:6379/2"

    # ── Qdrant ───────────────────────────────────────────────────────────────
    qdrant_host: str = "localhost"
    qdrant_port: int = 6333
    qdrant_collection_prefix: str = "tantra"

    # ── LiteLLM Proxy ────────────────────────────────────────────────────────
    litellm_base_url: str = "http://localhost:4000"
    litellm_api_key: SecretStr = Field(default="tantra-master-key")

    # ── Cloud LLM Keys ───────────────────────────────────────────────────────
    openai_api_key: Optional[SecretStr] = None
    anthropic_api_key: Optional[SecretStr] = None
    groq_api_key: Optional[SecretStr] = None
    together_api_key: Optional[SecretStr] = None

    # ── JWT ───────────────────────────────────────────────────────────────────
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 60
    jwt_refresh_token_expire_days: int = 30

    # ── Google OAuth (for login + YouTube) ───────────────────────────────────
    google_client_id: Optional[str] = None
    google_client_secret: Optional[SecretStr] = None
    # Separate scopes for login vs YouTube data access
    google_auth_scopes: str = "openid email profile"

    # ── GitHub OAuth ──────────────────────────────────────────────────────────
    github_client_id: Optional[str] = None
    github_client_secret: Optional[SecretStr] = None

    # ── Zernio (Unified Social Media API — primary publishing layer) ─────────
    # Sign up at https://zernio.com → connect LinkedIn/YouTube/X/etc via OAuth
    # (no individual developer app registrations needed)
    # Get your API key from https://zernio.com/dashboard/api-keys
    zernio_api_key: Optional[SecretStr] = None
    zernio_base_url: str = "https://zernio.com/api"
    # After connecting accounts in Zernio dashboard, get IDs from GET /v1/accounts
    # or run: curl -H "Authorization: Bearer $ZERNIO_API_KEY" https://zernio.com/api/v1/accounts
    zernio_linkedin_account_id: Optional[str] = None      # acc_xxxxxxxx
    zernio_youtube_account_id: Optional[str] = None       # acc_xxxxxxxx
    zernio_twitter_account_id: Optional[str] = None       # acc_xxxxxxxx
    zernio_instagram_account_id: Optional[str] = None     # acc_xxxxxxxx
    zernio_default_timezone: str = "Asia/Kolkata"

    @property
    def zernio_key(self) -> Optional[str]:
        return self.zernio_api_key.get_secret_value() if self.zernio_api_key else None

    @property
    def zernio_enabled(self) -> bool:
        return bool(self.zernio_api_key)

    # ── LinkedIn (direct API — fallback if Zernio not configured) ────────────
    linkedin_client_id: Optional[str] = None
    linkedin_client_secret: Optional[SecretStr] = None
    linkedin_redirect_uri: str = "http://localhost:8000/api/v1/auth/linkedin/callback"
    # LinkedIn OpenID Connect scopes (v2 API — replaces legacy r_liteprofile/r_emailaddress)
    #   openid  — enables /userinfo endpoint (sub, name, email, picture)
    #   profile — full name, profile picture
    #   email   — primary email address
    #   w_member_social — create/delete UGC posts
    linkedin_scopes: str = "openid profile email w_member_social"

    # ── n8n Workflow Automation ───────────────────────────────────────────────
    # n8n runs at localhost:5678 in the Docker stack
    # Phase 1: LinkedIn content draft webhook
    n8n_content_draft_webhook: str = "http://n8n:5678/webhook/tantra-content-draft"
    # Approval callback — n8n POSTs back here with approve/reject decision
    n8n_approval_callback_base: str = "http://tantra-api:8000/api/v1/content"
    # Phase 3: YouTube script approval webhook
    # Tantra API POSTs here when a YouTube script is ready for human review
    # Set N8N_YOUTUBE_SCRIPT_WEBHOOK in .env after importing the workflow into n8n
    n8n_youtube_script_webhook: Optional[str] = None

    # ── tantra-media (Phase 3b) ───────────────────────────────────────────────
    # Internal Docker network URL for the TTS + video assembly microservice
    tantra_media_url: str = "http://tantra-media:8100"

    # ── YouTube / Google Data API ─────────────────────────────────────────────
    youtube_api_key: Optional[SecretStr] = None
    youtube_client_id: Optional[str] = None
    youtube_client_secret: Optional[SecretStr] = None
    youtube_redirect_uri: str = "http://localhost:8000/api/v1/auth/youtube/callback"

    # ── Email (for password reset + verification) ─────────────────────────────
    smtp_host: Optional[str] = None
    smtp_port: int = 587
    smtp_user: Optional[str] = None
    smtp_password: Optional[SecretStr] = None
    smtp_from_email: str = "noreply@tantra.ai"
    smtp_tls: bool = True

    # ── Embedding ─────────────────────────────────────────────────────────────
    embed_model: str = "nomic-embed-text"
    embed_dim: int = 768
    embed_batch_size: int = 32

    # ── RAG ───────────────────────────────────────────────────────────────────
    rag_chunk_size: int = 512
    rag_chunk_overlap: int = 64
    rag_top_k: int = 5
    rag_score_threshold: float = 0.7

    # ── Agent ─────────────────────────────────────────────────────────────────
    agent_max_iterations: int = 10
    agent_memory_window: int = 20
    crew_verbose: bool = True

    # ── Logging ───────────────────────────────────────────────────────────────
    log_level: str = "INFO"
    log_format: str = "text"  # text (dev) | json (prod)

    # ── Feature flags ─────────────────────────────────────────────────────────
    enable_email_verification: bool = False  # set True when SMTP configured
    enable_web_terminal: bool = True

    @field_validator("database_url", mode="before")
    @classmethod
    def ensure_async_driver(cls, v: str) -> str:
        if v.startswith("postgresql://"):
            return v.replace("postgresql://", "postgresql+asyncpg://", 1)
        return v

    @property
    def is_production(self) -> bool:
        return self.environment == Environment.production

    @property
    def qdrant_url(self) -> str:
        return f"http://{self.qdrant_host}:{self.qdrant_port}"

    @property
    def litellm_key(self) -> str:
        return self.litellm_api_key.get_secret_value()


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
