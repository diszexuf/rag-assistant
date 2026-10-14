"""Общее для скриптов оценки: чтение наборов запросов, поиск станка, запись результатов."""

import json
from datetime import datetime
from pathlib import Path

import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.kb.models import Machine

EVAL_DIR = Path(__file__).parent
RESULTS_DIR = EVAL_DIR / "results"


def load_yaml(name: str) -> list[dict]:
    return yaml.safe_load((EVAL_DIR / name).read_text("utf-8"))


async def load_machines(session: AsyncSession) -> dict[str, Machine]:
    machines = (await session.execute(select(Machine))).scalars().all()
    return {m.name: m for m in machines}


def expected_titles(item: dict) -> list[str] | None:
    expected = item.get("expected")
    if expected is None:
        return None
    return [expected] if isinstance(expected, str) else list(expected)


def save_result(prefix: str, label: str, data: dict) -> Path:
    RESULTS_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = RESULTS_DIR / f"{prefix}-{stamp}-{label}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
    return path
