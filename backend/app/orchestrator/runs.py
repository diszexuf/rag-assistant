import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.chat.models import ACTIVE_STAGES, Chat, GenerationRun, Message, ProgramVersion
from app.kb.models import Machine, Program
from app.kb.refs import OP_TYPES
from app.kb.search import SearchFilters, search_operations
from app.llm.base import ChatModel, EmbeddingModel, LLMError
from app.orchestrator.pipeline import (
    GenerationError,
    MachineContext,
    OperationRequest,
    Pipeline,
    RunProgress,
    worst_kind,
    worst_origin,
)

logger = logging.getLogger("orchestrator.runs")

HEARTBEAT_S = 15


class RunManager:
    """Фоновые запуски генерации: стадии в БД и рассылка событий подписчикам."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        chat_model: Callable[[], ChatModel],
        embedder: Callable[[], EmbeddingModel],
        *,
        run_timeout_s: float,
        max_fix_attempts: int,
        search_top_k: int,
        search_max_distance: float,
    ):
        self._sessions = session_factory
        self._chat_model = chat_model
        self._embedder = embedder
        self._run_timeout_s = run_timeout_s
        self._max_fix_attempts = max_fix_attempts
        self._top_k = search_top_k
        self._max_distance = search_max_distance
        self._subscribers: dict[int, set[asyncio.Queue]] = {}
        self._tasks: dict[int, asyncio.Task] = {}

    async def start(self, session: AsyncSession, chat: Chat, message: Message) -> GenerationRun:
        run = GenerationRun(chat_id=chat.id, message_id=message.id, stage="queued")
        session.add(run)
        await session.commit()
        task = asyncio.create_task(self._execute(run.id))
        self._tasks[run.id] = task
        task.add_done_callback(lambda _: self._tasks.pop(run.id, None))
        return run

    async def fail_stale_runs(self) -> None:
        """После перезапуска сервера незавершённые запуски уже никто не выполняет."""
        async with self._sessions() as session:
            await session.execute(
                update(GenerationRun)
                .where(GenerationRun.stage.in_(ACTIVE_STAGES))
                .values(
                    stage="failed",
                    error="Сервер был перезапущен во время генерации",
                    finished_at=datetime.now(timezone.utc),
                )
            )
            await session.commit()

    async def stream(self, run_id: int) -> AsyncIterator[dict | None]:
        """Состояния запуска: сначала текущее из БД, затем изменения. None — служебный пульс."""
        queue: asyncio.Queue = asyncio.Queue()
        self._subscribers.setdefault(run_id, set()).add(queue)
        try:
            async with self._sessions() as session:
                run = await session.get(GenerationRun, run_id)
                state = run.state() if run else None
            if state is None:
                return
            yield state
            while not state["final"]:
                try:
                    state = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_S)
                except asyncio.TimeoutError:
                    yield None
                    continue
                yield state
        finally:
            subscribers = self._subscribers.get(run_id, set())
            subscribers.discard(queue)
            if not subscribers:
                self._subscribers.pop(run_id, None)

    async def _publish(self, state: dict) -> None:
        for queue in self._subscribers.get(state["run_id"], set()):
            queue.put_nowait(state)

    async def _update(self, run_id: int, **values) -> None:
        async with self._sessions() as session:
            run = await session.get(GenerationRun, run_id)
            for name, value in values.items():
                setattr(run, name, value)
            await session.commit()
            await self._publish(run.state())

    async def _execute(self, run_id: int) -> None:
        progress = RunProgress()
        try:
            async with asyncio.timeout(self._run_timeout_s):
                await self._generate(run_id, progress)
            error = None
        except TimeoutError:
            error = f"Генерация не уложилась в {self._run_timeout_s:g} с"
        except (LLMError, GenerationError) as exc:
            error = str(exc)
        except Exception as exc:  # запуск не должен остаться в активной стадии
            logger.exception("run %s failed", run_id)
            error = f"Внутренняя ошибка: {exc}"

        try:
            program_id = await self._save_result(run_id, progress, failed=error is not None)
            await self._update(
                run_id,
                stage="failed" if error else "done",
                error=error,
                program_id=program_id,
                finished_at=datetime.now(timezone.utc),
            )
        except Exception:
            logger.exception("run %s: could not save result", run_id)
            await self._update(run_id, stage="failed", error="Не удалось сохранить результат")

    async def _generate(self, run_id: int, progress: RunProgress) -> None:
        async with self._sessions() as session:
            run = await session.get(GenerationRun, run_id)
            chat = await session.get(Chat, run.chat_id)
            message = await session.get(Message, run.message_id)
            machine = await session.get(Machine, chat.machine_id)
            context = MachineContext(
                name=machine.name,
                kind=machine.kind,
                dialect=machine.dialect_code,
                max_spindle_rpm=machine.max_spindle_rpm,
                tools=machine.tools,
                notes=machine.notes,
            )
            material = chat.material
            operations = [
                OperationRequest(op["type"], op["description"]) for op in message.operations
            ]
            embedder = self._embedder()

            async def search(query: str, filters: SearchFilters):
                return await search_operations(
                    session, embedder, query, filters, self._top_k, self._max_distance
                )

            async def on_stage(stage: str, attempt: int) -> None:
                await self._update(run_id, stage=stage, attempt=attempt)

            pipeline = Pipeline(
                self._chat_model(), search, max_fix_attempts=self._max_fix_attempts
            )
            base = await session.get(Program, message.program_id) if message.program_id else None
            if base is None:
                await pipeline.run(context, material, operations, on_stage, progress)
            else:
                # Правка: берётся актуальный текст программы, включая ручные правки технолога
                progress.base_program_id = base.id
                await pipeline.edit(
                    context,
                    material,
                    base.text,
                    message.text,
                    operations,
                    on_stage,
                    progress,
                    first_index=len(base.sources) + 1,
                )

    async def _save_result(self, run_id: int, progress: RunProgress, failed: bool) -> int | None:
        """Сохраняет УП и ответ в чат. При сбое сохраняется черновик, если он успел появиться."""
        if not progress.program_text:
            return None
        async with self._sessions() as session:
            run = await session.get(GenerationRun, run_id)
            chat = await session.get(Chat, run.chat_id)
            machine = await session.get(Machine, chat.machine_id)
            request = await session.get(Message, run.message_id)
            report = progress.report.to_dict() if progress.report else {}
            note = "Черновик: генерация прервана. " if failed else ""
            if progress.base_program_id is not None:
                return await self._save_edit(session, chat, progress, report, note)
            program = Program(
                title=_title(request.operations),
                text=progress.program_text,
                machine_id=machine.id,
                dialect_code=machine.dialect_code,
                material=chat.material,
                status="generated",
                origin=worst_origin(progress.sources),
                chat_id=chat.id,
                sources=[s.to_dict() for s in progress.sources],
            )
            session.add(program)
            await session.flush()
            session.add(
                ProgramVersion(
                    program_id=program.id, text=program.text, author="system", report=report
                )
            )
            session.add(
                Message(
                    chat_id=chat.id,
                    role="assistant",
                    text=note + progress.explanation,
                    program_id=program.id,
                )
            )
            await session.commit()
            return program.id

    async def _save_edit(
        self, session: AsyncSession, chat: Chat, progress: RunProgress, report: dict, note: str
    ) -> int:
        """Результат правки — новая версия той же программы, а не новая программа."""
        program = await session.get(Program, progress.base_program_id)
        program.text = progress.program_text
        # Новый список: SQLAlchemy не отслеживает изменения JSON на месте
        program.sources = list(program.sources) + [s.to_dict() for s in progress.sources]
        if program.sources:
            program.origin = worst_kind(s["kind"] for s in program.sources)
        session.add(
            ProgramVersion(program_id=program.id, text=program.text, author="edit", report=report)
        )
        session.add(
            Message(
                chat_id=chat.id,
                role="assistant",
                text=note + progress.explanation,
                program_id=program.id,
            )
        )
        await session.commit()
        return program.id


def _title(operations: list[dict]) -> str:
    names = [OP_TYPES.get(op["type"], op["type"]) for op in operations]
    return ", ".join(names)[:200]


async def active_run(session: AsyncSession, chat_id: int) -> GenerationRun | None:
    result = await session.execute(
        select(GenerationRun)
        .where(GenerationRun.chat_id == chat_id, GenerationRun.stage.in_(ACTIVE_STAGES))
        .order_by(GenerationRun.id.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()
