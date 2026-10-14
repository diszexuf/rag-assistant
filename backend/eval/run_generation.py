"""Оценка генерации: python -m eval.run_generation [--repeats 3]

Прогоняет запросы из eval/requests.yaml через пайплайн с текущей моделью (LLM_PROVIDER)
без записи в базу и считает замечания валидатора по каждому прогону.
"""

import argparse
import asyncio
import statistics
import time
from collections import Counter

from app.config import get_settings
from app.db import SessionLocal
from app.kb.search import SearchFilters, search_operations
from app.llm.base import LLMError
from app.llm.factory import get_chat_model, get_embedding_model
from app.orchestrator.pipeline import (
    GenerationError,
    MachineContext,
    OperationRequest,
    Pipeline,
)
from eval.common import load_machines, load_yaml, save_result


async def run_once(session, embedder, machine, material, operations, settings) -> dict:
    async def search(query: str, filters: SearchFilters):
        return await search_operations(
            session, embedder, query, filters, settings.search_top_k, settings.search_max_distance
        )

    context = MachineContext(
        name=machine.name,
        kind=machine.kind,
        dialect=machine.dialect_code,
        max_spindle_rpm=machine.max_spindle_rpm,
        tools=machine.tools,
        notes=machine.notes,
    )
    pipeline = Pipeline(get_chat_model(), search, max_fix_attempts=settings.max_fix_attempts)
    started = time.monotonic()
    try:
        result = await pipeline.run(
            context, material, [OperationRequest(o["type"], o["description"]) for o in operations]
        )
    except (LLMError, GenerationError) as exc:
        return {"error": str(exc), "seconds": round(time.monotonic() - started, 1)}
    codes = Counter(i.code for i in result.report.issues)
    return {
        "errors": len(result.report.errors),
        "warnings": len(result.report.issues) - len(result.report.errors),
        "missing_values": codes.get("REQUEST_VALUE_MISSING", 0),
        "attempts": result.attempts,
        "origin": result.origin,
        "seconds": round(time.monotonic() - started, 1),
    }


async def main(repeats: int) -> None:
    settings = get_settings()
    embedder = get_embedding_model()
    report: list[dict] = []

    async with SessionLocal() as session:
        machines = await load_machines(session)
        for item in load_yaml("requests.yaml"):
            machine = machines[item["machine"]]
            runs = [
                await run_once(session, embedder, machine, item["material"], item["operations"], settings)
                for _ in range(repeats)
            ]
            report.append({"id": item["id"], "runs": runs})
            ok = [r for r in runs if "error" not in r]
            print(f"{item['id']}: прогонов {len(runs)}, с ошибкой модели {len(runs) - len(ok)}")
            for name in ("errors", "warnings", "missing_values", "attempts", "seconds"):
                if ok:
                    print(f"  {name}: среднее {statistics.mean(r[name] for r in ok):.2f}")

    label = settings.llm_provider
    if settings.llm_provider == "gigachat":
        label += "-" + settings.gigachat_model
    path = save_result("generation", label, {"repeats": repeats, "requests": report})
    print(f"Результат: {path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    asyncio.run(main(args.repeats))
