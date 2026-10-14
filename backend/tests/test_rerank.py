import json

import httpx
import pytest

from app.kb.search import OperationHit, apply_rerank
from app.llm.base import LLMError
from app.llm.tei import TEIReranker


def hit(operation_id: int, distance: float) -> OperationHit:
    return OperationHit(operation_id, 1, f"УП {operation_id}", "fanuc_turn", "turning", "d", "code", distance)


def test_apply_rerank_orders_by_score_filters_and_limits():
    hits = [hit(1, 0.1), hit(2, 0.2), hit(3, 0.3), hit(4, 0.4)]
    result = apply_rerank(hits, [0.2, 0.9, 0.5, 0.7], min_score=0.3, top_k=2)
    assert [h.operation_id for h in result] == [2, 4]
    assert [h.score for h in result] == [0.9, 0.7]
    # Расстояние сохраняется для интерфейса
    assert result[0].distance == 0.2


def test_apply_rerank_returns_nothing_when_all_below_threshold():
    assert apply_rerank([hit(1, 0.1)], [0.05], min_score=0.3, top_k=3) == []


def test_reranker_can_promote_a_farther_candidate():
    # Ближайший по вектору кандидат оказывается хуже по оценке cross-encoder
    hits = [hit(1, 0.10), hit(2, 0.35)]
    assert apply_rerank(hits, [0.4, 0.8], 0.3, 1)[0].operation_id == 2


async def test_reranker_scores_in_input_order():
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert request.url.path == "/rerank" and payload["query"] == "q" and payload["truncate"] is True
        # TEI отдаёт результаты, отсортированные по убыванию оценки
        return httpx.Response(
            200, json=[{"index": 2, "score": 0.9}, {"index": 0, "score": 0.4}, {"index": 1, "score": 0.1}]
        )

    reranker = TEIReranker("http://tei", 5, transport=httpx.MockTransport(handler))
    assert await reranker.rerank("q", ["a", "b", "c"]) == [0.4, 0.1, 0.9]
    assert await reranker.rerank("q", []) == []


async def test_reranker_bad_response_is_llm_error():
    reranker = TEIReranker(
        "http://tei", 5, transport=httpx.MockTransport(lambda r: httpx.Response(200, json=[{"index": 0}]))
    )
    with pytest.raises(LLMError, match="реранкера"):
        await reranker.rerank("q", ["a"])
