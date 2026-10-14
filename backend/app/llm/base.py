from dataclasses import dataclass
from typing import Literal, Protocol


class LLMError(Exception):
    """Модель недоступна или вернула ошибку."""


@dataclass(frozen=True)
class ChatMessage:
    role: Literal["system", "user", "assistant"]
    content: str


class ChatModel(Protocol):
    async def complete(self, messages: list[ChatMessage], **params) -> str: ...


class EmbeddingModel(Protocol):
    async def embed(self, texts: list[str]) -> list[list[float]]: ...
