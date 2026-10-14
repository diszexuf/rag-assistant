from dataclasses import dataclass, replace

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import contains_eager

from app.kb.ingest import embedding_text
from app.kb.models import Machine, Operation, Program
from app.llm.base import EmbeddingModel, Reranker


@dataclass(frozen=True)
class SearchFilters:
    machine_kind: str
    op_type: str
    # Ровно один из двух: искать на этом диалекте либо на любом, кроме него
    dialect: str | None = None
    exclude_dialect: str | None = None
    material: str | None = None


@dataclass(frozen=True)
class OperationHit:
    operation_id: int
    program_id: int
    program_title: str
    dialect: str
    op_type: str
    description: str
    fragment: str
    distance: float
    # Оценка реранкера 0..1; None, если реранкер не использовался
    score: float | None = None


def apply_rerank(
    hits: list[OperationHit], scores: list[float], min_score: float, top_k: int
) -> list[OperationHit]:
    """Оставляет кандидатов с оценкой не ниже порога, лучшие первыми, не больше top_k."""
    scored = [replace(hit, score=score) for hit, score in zip(hits, scores)]
    relevant = [hit for hit in scored if hit.score is not None and hit.score >= min_score]
    return sorted(relevant, key=lambda hit: hit.score, reverse=True)[:top_k]


async def search_operations(
    session: AsyncSession,
    embedder: EmbeddingModel,
    query: str,
    filters: SearchFilters,
    top_k: int,
    max_distance: float,
    *,
    reranker: Reranker | None = None,
    candidates: int = 20,
    min_score: float = 0.3,
) -> list[OperationHit]:
    """Фильтр по метаданным, затем векторный поиск.

    Без реранкера кандидаты отсекаются по расстоянию max_distance. С реранкером берётся
    `candidates` ближайших без отсечения по расстоянию, их переоценивает cross-encoder,
    а релевантность определяет min_score.
    """
    vector = (await embedder.embed([query]))[0]
    distance = Operation.embedding.cosine_distance(vector).label("distance")
    statement = (
        select(Operation, distance)
        .join(Operation.program)
        .join(Program.machine)
        .options(contains_eager(Operation.program))
        .where(
            Program.status == "confirmed",
            Machine.kind == filters.machine_kind,
            Operation.op_type == filters.op_type,
        )
        .order_by(distance)
        .limit(candidates if reranker is not None else top_k)
    )
    if filters.dialect:
        statement = statement.where(Program.dialect_code == filters.dialect)
    if filters.exclude_dialect:
        statement = statement.where(Program.dialect_code != filters.exclude_dialect)
    if filters.material:
        statement = statement.where(Program.material == filters.material)

    hits = []
    for operation, dist in (await session.execute(statement)).all():
        if reranker is None and dist > max_distance:
            continue
        hits.append(
            OperationHit(
                operation_id=operation.id,
                program_id=operation.program_id,
                program_title=operation.program.title,
                dialect=operation.program.dialect_code,
                op_type=operation.op_type,
                description=operation.description,
                fragment=operation.fragment(),
                distance=float(dist),
            )
        )
    if reranker is None or not hits:
        return hits

    # Код фрагмента помогает отличать циклы и режимы, которых нет в описании
    texts = [f"{embedding_text(h.op_type, h.description)}\n{h.fragment}" for h in hits]
    scores = await reranker.rerank(query, texts)
    return apply_rerank(hits, scores, min_score, top_k)
