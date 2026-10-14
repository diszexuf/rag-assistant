from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import contains_eager

from app.kb.models import Machine, Operation, Program
from app.llm.base import EmbeddingModel


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


async def search_operations(
    session: AsyncSession,
    embedder: EmbeddingModel,
    query: str,
    filters: SearchFilters,
    top_k: int,
    max_distance: float,
) -> list[OperationHit]:
    """Фильтр по метаданным, затем векторный поиск с отсечением по расстоянию."""
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
        .limit(top_k)
    )
    if filters.dialect:
        statement = statement.where(Program.dialect_code == filters.dialect)
    if filters.exclude_dialect:
        statement = statement.where(Program.dialect_code != filters.exclude_dialect)
    if filters.material:
        statement = statement.where(Program.material == filters.material)

    hits = []
    for operation, dist in (await session.execute(statement)).all():
        if dist > max_distance:
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
    return hits
