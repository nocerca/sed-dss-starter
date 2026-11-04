from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    database_url: str = "sqlite:///./local-dev.sqlite3"
    app_api_key: str = "local-dev-only-change-before-exposure"
    llm_provider: str = "none"
    allow_external_llm: bool = False
    llm_timeout_seconds: int = 60
    llm_max_retries: int = Field(default=0, ge=0, le=2)
    max_document_bytes: int = 5 * 1024 * 1024
    idempotency_secret: str = ""
    openclaw_base_url: str = "http://openclaw:18789/v1"
    openclaw_api_key: str = ""
    openclaw_model: str = "openclaw/sed-dss"
    language_tool_enabled: bool = False
    language_tool_base_url: str = "http://languagetool:8081/v2"
    language_tool_timeout_seconds: int = Field(default=20, ge=1, le=60)
    analysis_cache_enabled: bool = True
    analysis_cache_ttl_seconds: int = Field(default=900, ge=1, le=86400)
    analysis_cache_max_entries: int = Field(default=128, ge=1, le=4096)
    model_backend: str = 'yandex'
    yandex_model: str = 'aliceai-llm-flash'
    gigachat_model: str = 'GigaChat-2'
    zai_model: str = 'glm-4.7-flash'


@lru_cache
def get_settings() -> Settings:
    return Settings()
