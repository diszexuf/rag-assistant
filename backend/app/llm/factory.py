from functools import lru_cache

from app.config import get_settings
from app.llm.base import ChatModel, EmbeddingModel
from app.llm.gigachat import GigaChatClient
from app.llm.mock import DemoChatModel, HashEmbeddingModel


@lru_cache
def _gigachat() -> GigaChatClient:
    settings = get_settings()
    return GigaChatClient(
        auth_key=settings.gigachat_auth_key,
        scope=settings.gigachat_scope,
        model=settings.gigachat_model,
        embedding_model=settings.gigachat_embedding_model,
        timeout_s=settings.llm_timeout_s,
        verify_ssl=settings.gigachat_verify_ssl,
        ca_bundle=settings.gigachat_ca_bundle,
    )


def get_chat_model() -> ChatModel:
    if get_settings().llm_provider == "gigachat":
        return _gigachat()
    return DemoChatModel()


def get_embedding_model() -> EmbeddingModel:
    settings = get_settings()
    if settings.embedding_provider == "gigachat":
        return _gigachat()
    return HashEmbeddingModel(settings.embedding_dim)
