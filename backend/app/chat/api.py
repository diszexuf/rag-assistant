import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app import nc
from app.chat.models import Chat, GenerationRun, Message, ProgramVersion, User
from app.db import get_session
from app.kb.models import Machine, Program
from app.kb.refs import MATERIALS, OP_TYPES
from app.orchestrator.params import missing_value_issues
from app.orchestrator.runs import RunManager, active_run

router = APIRouter(prefix="/api")
logger = logging.getLogger("api")

DEFAULT_LOGIN = "technologist"


class OperationIn(BaseModel):
    type: str
    description: str = Field(min_length=3, max_length=2000)


class RequestIn(BaseModel):
    # Новая программа: непустой список операций. Правка: base_program_id и инструкция и/или операции
    operations: list[OperationIn] = Field(default_factory=list, max_length=20)
    base_program_id: int | None = None
    instruction: str = Field(default="", max_length=2000)


class ChatIn(BaseModel):
    machine_id: int
    material: str
    title: str = ""


class VersionIn(BaseModel):
    text: str = Field(min_length=1)


def get_runs(request: Request) -> RunManager:
    return request.app.state.runs


async def current_user(session: AsyncSession = Depends(get_session)) -> User:
    """В ядре MVP входа нет: один предустановленный технолог."""
    user = (await session.execute(select(User).where(User.login == DEFAULT_LOGIN))).scalar_one_or_none()
    if user is None:
        user = User(login=DEFAULT_LOGIN, role="technologist")
        session.add(user)
        await session.commit()
    return user


def _machine(machine: Machine) -> dict:
    return {
        "id": machine.id,
        "name": machine.name,
        "kind": machine.kind,
        "dialect": machine.dialect_code,
        "dialect_title": nc.load_dialect(machine.dialect_code).title,
        "max_spindle_rpm": machine.max_spindle_rpm,
        "tools": machine.tools,
    }


def _chat(chat: Chat) -> dict:
    return {
        "id": chat.id,
        "title": chat.title,
        "machine_id": chat.machine_id,
        "material": chat.material,
        "created_at": chat.created_at.isoformat(),
    }


def _version(version: ProgramVersion) -> dict:
    return {
        "id": version.id,
        "author": version.author,
        "text": version.text,
        "report": version.report,
        "created_at": version.created_at.isoformat(),
    }


@router.get("/refs")
async def refs(session: AsyncSession = Depends(get_session)) -> dict:
    machines = (await session.execute(select(Machine).order_by(Machine.id))).scalars().all()
    return {
        "machines": [_machine(m) for m in machines],
        "materials": MATERIALS,
        "op_types": OP_TYPES,
    }


@router.get("/chats")
async def list_chats(
    user: User = Depends(current_user), session: AsyncSession = Depends(get_session)
) -> list[dict]:
    chats = await session.execute(
        select(Chat).where(Chat.user_id == user.id).order_by(Chat.id.desc())
    )
    return [_chat(c) for c in chats.scalars()]


@router.post("/chats", status_code=201)
async def create_chat(
    body: ChatIn, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)
) -> dict:
    machine = await session.get(Machine, body.machine_id)
    if machine is None:
        raise HTTPException(404, "Станок не найден")
    if body.material not in MATERIALS:
        raise HTTPException(422, "Неизвестный материал")
    title = body.title.strip() or f"{machine.name}, {MATERIALS[body.material].lower()}"
    chat = Chat(user_id=user.id, title=title[:200], machine_id=machine.id, material=body.material)
    session.add(chat)
    await session.commit()
    logger.info(
        "Создан чат %s: станок «%s», материал %s", chat.id, machine.name, body.material
    )
    return _chat(chat)


async def _get_chat(chat_id: int, user: User, session: AsyncSession) -> Chat:
    chat = (
        await session.execute(
            select(Chat)
            .where(Chat.id == chat_id, Chat.user_id == user.id)
            .options(selectinload(Chat.messages), selectinload(Chat.machine))
        )
    ).scalar_one_or_none()
    if chat is None:
        raise HTTPException(404, "Чат не найден")
    return chat


@router.get("/chats/{chat_id}")
async def get_chat(
    chat_id: int, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)
) -> dict:
    chat = await _get_chat(chat_id, user, session)
    run = await active_run(session, chat.id)
    last_run = (
        await session.execute(
            select(GenerationRun)
            .where(GenerationRun.chat_id == chat.id)
            .order_by(GenerationRun.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return {
        **_chat(chat),
        "machine": _machine(chat.machine),
        "messages": [
            {
                "id": m.id,
                "role": m.role,
                "text": m.text,
                "operations": m.operations,
                "program_id": m.program_id,
                "created_at": m.created_at.isoformat(),
            }
            for m in chat.messages
        ],
        "active_run": run.state() if run else None,
        "last_run": last_run.state() if last_run else None,
    }


@router.delete("/chats/{chat_id}", status_code=204)
async def delete_chat(
    chat_id: int, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)
) -> None:
    chat = await _get_chat(chat_id, user, session)
    if await active_run(session, chat.id):
        raise HTTPException(409, "В чате идёт генерация")
    await session.delete(chat)
    await session.commit()
    logger.info("Удалён чат %s", chat_id)


@router.post("/chats/{chat_id}/requests", status_code=202)
async def create_request(
    chat_id: int,
    body: RequestIn,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
    runs: RunManager = Depends(get_runs),
) -> dict:
    chat = await _get_chat(chat_id, user, session)
    unknown = [op.type for op in body.operations if op.type not in OP_TYPES]
    if unknown:
        raise HTTPException(422, f"Неизвестный тип операции: {unknown[0]}")
    if await active_run(session, chat.id):
        raise HTTPException(409, "В чате уже идёт генерация")
    instruction = body.instruction.strip()
    if body.base_program_id is None:
        if not body.operations:
            raise HTTPException(422, "Укажите хотя бы одну операцию")
        instruction = ""
    else:
        program = await _get_program(body.base_program_id, user, session)
        if program.chat_id != chat.id:
            raise HTTPException(404, "Программа не найдена")
        if len(instruction) < 3 and not body.operations:
            raise HTTPException(422, "Опишите, что изменить, или добавьте операции")
    operations = [{"type": op.type, "description": op.description.strip()} for op in body.operations]
    message = Message(
        chat_id=chat.id,
        role="user",
        text=instruction,
        operations=operations,
        program_id=body.base_program_id,
    )
    session.add(message)
    await session.flush()
    logger.info(
        "Запрос в чате %s: %s, операций %s%s",
        chat.id,
        "правка программы %s" % body.base_program_id if body.base_program_id else "новая программа",
        len(operations),
        f", инструкция: {instruction[:120]}" if instruction else "",
    )
    run = await runs.start(session, chat, message)
    return run.state()


@router.get("/runs/{run_id}")
async def get_run(run_id: int, session: AsyncSession = Depends(get_session)) -> dict:
    run = await session.get(GenerationRun, run_id)
    if run is None:
        raise HTTPException(404, "Запуск не найден")
    return run.state()


@router.get("/runs/{run_id}/events")
async def run_events(
    run_id: int, session: AsyncSession = Depends(get_session), runs: RunManager = Depends(get_runs)
) -> StreamingResponse:
    if await session.get(GenerationRun, run_id) is None:
        raise HTTPException(404, "Запуск не найден")

    async def events():
        async for state in runs.stream(run_id):
            if state is None:
                yield ": ping\n\n"
            else:
                yield f"data: {json.dumps(state, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _get_program(program_id: int, user: User, session: AsyncSession) -> Program:
    program = (
        await session.execute(
            select(Program)
            .join(Chat, Chat.id == Program.chat_id)
            .where(Program.id == program_id, Chat.user_id == user.id)
            .options(selectinload(Program.machine))
        )
    ).scalar_one_or_none()
    if program is None:
        raise HTTPException(404, "Программа не найдена")
    return program


@router.get("/programs/{program_id}")
async def get_program(
    program_id: int, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)
) -> dict:
    program = await _get_program(program_id, user, session)
    versions = await session.execute(
        select(ProgramVersion)
        .where(ProgramVersion.program_id == program.id)
        .order_by(ProgramVersion.id)
    )
    return {
        "id": program.id,
        "title": program.title,
        "status": program.status,
        "origin": program.origin,
        "dialect": program.dialect_code,
        "sources": program.sources,
        "versions": [_version(v) for v in versions.scalars()],
    }


@router.post("/programs/{program_id}/versions", status_code=201)
async def save_version(
    program_id: int,
    body: VersionIn,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Ручная правка: сохраняется всегда, замечания валидатора только показываются."""
    program = await _get_program(program_id, user, session)
    report = nc.validate(body.text, program.dialect_code, program.machine.info())
    operations = [(s["op_type"], s["description"]) for s in program.sources]
    report.issues.extend(missing_value_issues(body.text, program.dialect_code, operations))
    report.issues.sort(key=lambda issue: issue.line)
    version = ProgramVersion(
        program_id=program.id, text=body.text, author="user", report=report.to_dict()
    )
    program.text = body.text
    session.add(version)
    await session.commit()
    logger.info(
        "Ручная правка программы %s: версия %s, ошибок %s, предупреждений %s",
        program.id,
        version.id,
        len(report.errors),
        len(report.issues) - len(report.errors),
    )
    return _version(version)
