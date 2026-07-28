"""Environment-driven BizOrch configuration."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Validated process configuration with no embedded credentials."""

    app_name: str = "BizOrch"
    app_version: str = "0.1.0"
    environment: str = "development"
    debug: bool = False
    api_prefix: str = "/api/v1"
    auth_session_ttl_hours: int = Field(default=8, ge=1, le=168)

    database_url: str = ""
    redis_url: str = ""
    chroma_path: Path = Path("data/chroma")
    knowledge_upload_path: Path = Path("data/uploads")
    knowledge_index_poll_interval_seconds: float = Field(
        default=2.0,
        ge=0.1,
        le=60.0,
    )
    knowledge_index_lease_seconds: int = Field(default=300, ge=30, le=1800)
    knowledge_index_retry_base_seconds: int = Field(default=5, ge=1, le=300)
    evaluation_live_max_cases: int = Field(default=20, ge=1, le=20)
    evaluation_live_max_calls: int = Field(default=40, ge=1, le=100)
    evaluation_live_max_input_tokens: int = Field(
        default=200_000,
        ge=1_000,
        le=2_000_000,
    )
    evaluation_live_max_output_tokens: int = Field(
        default=50_000,
        ge=1_000,
        le=500_000,
    )
    evaluation_live_max_embedding_texts: int = Field(
        default=100,
        ge=1,
        le=1_000,
    )
    evaluation_live_case_timeout_seconds: int = Field(
        default=60,
        ge=5,
        le=300,
    )
    evaluation_live_run_timeout_seconds: int = Field(
        default=1_200,
        ge=60,
        le=7_200,
    )
    checkpoint_path: Path = Path("data/checkpoints/langgraph.db")
    enterprise_ops_base_url: str = "http://127.0.0.1:8100"
    enterprise_ops_mcp_url: str = "http://127.0.0.1:8200/mcp"
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-v4-flash"
    oss_bucket_name: str = ""
    oss_endpoint: str = ""
    oss_avatar_prefix: str = "bizorch/avatars"

    deepseek_api_key: SecretStr | None = Field(
        default=None,
        validation_alias="DEEPSEEK_API_KEY",
    )
    dashscope_api_key: SecretStr | None = Field(
        default=None,
        validation_alias="DASHSCOPE_API_KEY",
    )
    enterprise_internal_token: SecretStr | None = Field(
        default=None,
        validation_alias="ENTERPRISE_INTERNAL_TOKEN",
    )
    oss_access_key_id: SecretStr | None = Field(
        default=None,
        validation_alias="BIZORCH_OSS_ACCESS_KEY_ID",
    )
    oss_access_key_secret: SecretStr | None = Field(
        default=None,
        validation_alias="BIZORCH_OSS_ACCESS_KEY_SECRET",
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="BIZORCH_",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    """Return one immutable-by-convention settings object per process."""
    return Settings()
