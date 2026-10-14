from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.config import get_settings
from app.db import Base
from app.nc import MachineInfo

EMBEDDING_DIM = get_settings().embedding_dim


class Dialect(Base):
    __tablename__ = "dialects"

    # Совпадает с именем файла описания в app/nc/dialects
    code: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str] = mapped_column(String(200))


class Machine(Base):
    __tablename__ = "machines"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    kind: Mapped[str] = mapped_column(String(32))
    dialect_code: Mapped[str] = mapped_column(ForeignKey("dialects.code"))
    max_spindle_rpm: Mapped[int] = mapped_column(Integer)
    # [{"position": 1, "name": "...", "type": "..."}]
    tools: Mapped[list] = mapped_column(JSON, default=list)
    notes: Mapped[str] = mapped_column(Text, default="")

    def info(self) -> MachineInfo:
        return MachineInfo(
            max_spindle_rpm=self.max_spindle_rpm,
            tool_positions=frozenset(int(t["position"]) for t in self.tools),
        )


class DocChunk(Base):
    __tablename__ = "doc_chunks"

    id: Mapped[int] = mapped_column(primary_key=True)
    dialect_code: Mapped[str | None] = mapped_column(ForeignKey("dialects.code"), nullable=True)
    machine_id: Mapped[int | None] = mapped_column(ForeignKey("machines.id"), nullable=True)
    source: Mapped[str] = mapped_column(String(200))
    text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIM))


class Program(Base):
    __tablename__ = "programs"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    text: Mapped[str] = mapped_column(Text)
    machine_id: Mapped[int] = mapped_column(ForeignKey("machines.id"))
    dialect_code: Mapped[str] = mapped_column(ForeignKey("dialects.code"))
    material: Mapped[str] = mapped_column(String(32))
    # generated | on_review | confirmed | rejected
    status: Mapped[str] = mapped_column(String(16), index=True)
    # kb | converted | unknown | imported
    origin: Mapped[str] = mapped_column(String(16))
    chat_id: Mapped[int | None] = mapped_column(
        ForeignKey("chats.id", ondelete="CASCADE"), nullable=True
    )
    # Источники по операциям запроса — для сгенерированных УП
    sources: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    machine: Mapped[Machine] = relationship()
    operations: Mapped[list["Operation"]] = relationship(
        back_populates="program", cascade="all, delete-orphan"
    )


class Operation(Base):
    __tablename__ = "operations"

    id: Mapped[int] = mapped_column(primary_key=True)
    program_id: Mapped[int] = mapped_column(ForeignKey("programs.id", ondelete="CASCADE"))
    op_type: Mapped[str] = mapped_column(String(32), index=True)
    line_start: Mapped[int] = mapped_column(Integer)
    line_end: Mapped[int] = mapped_column(Integer)
    description: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIM))

    program: Mapped[Program] = relationship(back_populates="operations")

    def fragment(self) -> str:
        lines = self.program.text.splitlines()
        return "\n".join(lines[self.line_start - 1 : self.line_end])
