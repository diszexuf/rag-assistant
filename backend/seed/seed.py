"""Наполнение базы знаний: python -m seed.seed [--reset]

Без флага добавляет только отсутствующие записи. С --reset удаляет загруженные ранее
УП базы знаний и загружает их заново (нужно после смены модели эмбеддингов).
"""

import asyncio
import sys
from pathlib import Path

import yaml
from sqlalchemy import delete, select

from app import nc
from app.chat.models import User
from app.db import SessionLocal
from app.kb.ingest import OperationSpec, load_program
from app.kb.models import Dialect, Machine, Program
from app.llm.factory import get_embedding_model

SEED_DIR = Path(__file__).parent


def assemble(spec: dict) -> tuple[str, list[OperationSpec]]:
    """Склеивает УП из шапки, операций и концовки, попутно вычисляя строки операций."""
    lines = spec["header"].splitlines()
    operations = []
    for op in spec["operations"]:
        code = op["code"].splitlines()
        operations.append(
            OperationSpec(
                op_type=op["type"],
                description=" ".join(op["description"].split()),
                line_start=len(lines) + 1,
                line_end=len(lines) + len(code),
            )
        )
        lines += code
    lines += spec["footer"].splitlines()
    return "\n".join(lines), operations


async def main(reset: bool) -> None:
    embedder = get_embedding_model()
    async with SessionLocal() as session:
        if await session.scalar(select(User).where(User.login == "technologist")) is None:
            session.add(User(login="technologist", role="technologist"))

        for code in nc.list_dialects():
            if await session.get(Dialect, code) is None:
                session.add(Dialect(code=code, title=nc.load_dialect(code).title))
        await session.flush()

        machines: dict[str, Machine] = {}
        for item in yaml.safe_load((SEED_DIR / "data" / "machines.yaml").read_text("utf-8")):
            machine = await session.scalar(select(Machine).where(Machine.name == item["name"]))
            if machine is None:
                machine = Machine(name=item["name"])
                session.add(machine)
            machine.kind = item["kind"]
            machine.dialect_code = item["dialect"]
            machine.max_spindle_rpm = item["max_spindle_rpm"]
            machine.tools = item["tools"]
            machine.notes = item.get("notes", "")
            machines[machine.name] = machine
        await session.flush()

        if reset:
            await session.execute(delete(Program).where(Program.origin == "imported"))

        for path in sorted((SEED_DIR / "programs").glob("*.yaml")):
            spec = yaml.safe_load(path.read_text("utf-8"))
            if await session.scalar(select(Program).where(Program.title == spec["title"])):
                print(f"= {spec['title']}: уже есть")
                continue
            machine = machines[spec["machine"]]
            text, operations = assemble(spec)
            report = nc.validate(text, machine.dialect_code, machine.info())
            for issue in report.issues:
                print(f"  ! {path.name}:{issue.line} [{issue.severity}] {issue.message}")
            await load_program(
                session,
                embedder,
                title=spec["title"],
                text=text,
                machine=machine,
                material=spec["material"],
                operations=operations,
            )
            print(f"+ {spec['title']}: операций {len(operations)}, ошибок {len(report.errors)}")

        await session.commit()


if __name__ == "__main__":
    asyncio.run(main(reset="--reset" in sys.argv))
