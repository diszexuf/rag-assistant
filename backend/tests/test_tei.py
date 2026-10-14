import json

import httpx
import pytest

from app.llm.base import LLMError
from app.llm.tei import BATCH_SIZE, TEIEmbeddingModel


def make_model(handler, dim=None) -> TEIEmbeddingModel:
    return TEIEmbeddingModel(
        "http://tei", timeout_s=5, dim=dim, transport=httpx.MockTransport(handler)
    )


async def test_embed_batches_and_keeps_order():
    batches: list[list[str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert request.url.path == "/embed" and payload["normalize"] is True
        batches.append(payload["inputs"])
        # Вектор кодирует номер текста, чтобы проверить порядок
        return httpx.Response(200, json=[[float(text.removeprefix("t"))] for text in payload["inputs"]])

    texts = [f"t{i}" for i in range(BATCH_SIZE * 2 + 5)]
    vectors = await make_model(handler).embed(texts)
    assert [len(b) for b in batches] == [BATCH_SIZE, BATCH_SIZE, 5]
    assert vectors == [[float(i)] for i in range(len(texts))]


async def test_server_error_becomes_llm_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="model is loading")

    with pytest.raises(LLMError, match="503"):
        await make_model(handler).embed(["a"])


async def test_unavailable_service_becomes_llm_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("нет соединения")

    with pytest.raises(LLMError, match="недоступен"):
        await make_model(handler).embed(["a"])


async def test_wrong_response_shape_and_dimension():
    with pytest.raises(LLMError, match="Неожиданный ответ"):
        await make_model(lambda r: httpx.Response(200, json=[[1.0]])).embed(["a", "b"])
    with pytest.raises(LLMError, match="EMBEDDING_DIM"):
        await make_model(lambda r: httpx.Response(200, json=[[1.0, 2.0]]), dim=1024).embed(["a"])
