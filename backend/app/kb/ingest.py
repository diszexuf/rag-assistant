from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.kb.models import Machine, Operation, Program
from app.kb.refs import OP_TYPES
from app.llm.base import EmbeddingModel


@dataclass(frozen=True)
class OperationSpec:
    op_type: str
    description: str
    line_start: int
    line_end: int


def embedding_text(op_type: str, description: str) -> str:
    """Текст, по которому строится вектор операции и вектор запроса."""
    return f"{OP_TYPES.get(op_type, op_type)}. {description}"


async def load_program(
    session: AsyncSession,
    embedder: EmbeddingModel,
    *,
    title: str,
    text: str,
    machine: Machine,
    material: str,
    operations: list[OperationSpec],
    status: str = "confirmed",
    origin: str = "imported",
) -> Program:
    vectors = await embedder.embed([embedding_text(o.op_type, o.description) for o in operations])
    program = Program(
        title=title,
        text=text,
        machine_id=machine.id,
        dialect_code=machine.dialect_code,
        material=material,
        status=status,
        origin=origin,
    )
    program.operations = [
        Operation(
            op_type=spec.op_type,
            line_start=spec.line_start,
            line_end=spec.line_end,
            description=spec.description,
            embedding=vector,
        )
        for spec, vector in zip(operations, vectors)
    ]
    session.add(program)
    await session.flush()
    return program
