"""Клиенты Hugging Face Text Embeddings Inference (TEI): эмбеддинги и реранкер."""

import httpx

from app.llm.base import LLMError

# Лимит TEI по умолчанию — 32 текста в одном запросе
BATCH_SIZE = 32


class _TEIClient:
    def __init__(
        self,
        url: str,
        timeout_s: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self._url = url.rstrip("/")
        self._http = httpx.AsyncClient(timeout=timeout_s, transport=transport)

    async def _post(self, path: str, payload: dict):
        try:
            response = await self._http.post(f"{self._url}{path}", json=payload)
        except httpx.HTTPError as exc:
            raise LLMError(
                f"Сервис моделей поиска недоступен ({self._url}): {exc!r}. "
                "Проверьте, что контейнер запущен и модель загрузилась."
            ) from exc
        if response.status_code >= 400:
            raise LLMError(
                f"Сервис моделей поиска вернул {response.status_code}: {response.text[:300]}"
            )
        return response.json()

    async def aclose(self) -> None:
        await self._http.aclose()


class TEIEmbeddingModel(_TEIClient):
    """Эмбеддинги через TEI. Реализует EmbeddingModel."""

    def __init__(
        self,
        url: str,
        timeout_s: float,
        dim: int | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        super().__init__(url, timeout_s, transport)
        self._dim = dim

    async def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), BATCH_SIZE):
            batch = texts[start : start + BATCH_SIZE]
            data = await self._post(
                "/embed", {"inputs": batch, "normalize": True, "truncate": True}
            )
            if not isinstance(data, list) or len(data) != len(batch):
                raise LLMError(f"Неожиданный ответ сервиса эмбеддингов: {str(data)[:200]}")
            vectors.extend(data)
        if vectors and self._dim is not None and len(vectors[0]) != self._dim:
            raise LLMError(
                f"Размерность эмбеддингов {len(vectors[0])} не совпадает с EMBEDDING_DIM={self._dim}"
            )
        return vectors
