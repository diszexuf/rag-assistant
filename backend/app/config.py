from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://rag:rag@db:5432/rag"

    llm_provider: Literal["mock", "gigachat"] = "mock"
    # Эмбеддинги выбираются отдельно: у GigaChat они оплачиваются отдельным пакетом
    embedding_provider: Literal["mock", "gigachat"] = "mock"
    gigachat_auth_key: str = ""
    gigachat_scope: str = "GIGACHAT_API_PERS"
    gigachat_model: str = "GigaChat"
    gigachat_embedding_model: str = "Embeddings"
    gigachat_verify_ssl: bool = True
    gigachat_ca_bundle: str = ""

    embedding_dim: int = 1024

    search_max_distance: float = 0.5
    search_top_k: int = 3

    max_fix_attempts: int = 3
    llm_timeout_s: float = 90
    run_timeout_s: float = 280


@lru_cache
def get_settings() -> Settings:
    return Settings()
