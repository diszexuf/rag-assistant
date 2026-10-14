"""Оценка поиска: python -m eval.run_search [--max-distance 0.5] [--top-k 5]

Без модели генерации: только фильтр, векторный поиск и порог. Запросы — eval/queries.yaml.
"""

import argparse
import asyncio

from app.config import get_settings
from app.db import SessionLocal
from app.kb.ingest import embedding_text
from app.kb.search import SearchFilters, search_operations
from app.llm.factory import get_embedding_model
from eval.common import expected_titles, load_machines, load_yaml, save_result


def rank_of(titles: list[str], expected: list[str]) -> int | None:
    """Ранг (с 1) первого кандидата из ожидаемых или None."""
    for index, title in enumerate(titles, start=1):
        if title in expected:
            return index
    return None


def compute_metrics(rows: list[dict], threshold: float) -> dict:
    """rows: {"id", "expected": list | None, "hits": [{"title", "distance"}]}."""
    positives = [r for r in rows if r["expected"] is not None]
    negatives = [r for r in rows if r["expected"] is None]

    ranks = [rank_of([h["title"] for h in r["hits"]], r["expected"]) for r in positives]
    n = max(len(positives), 1)
    metrics = {
        "positives": len(positives),
        "negatives": len(negatives),
        "hit@1": sum(1 for x in ranks if x == 1) / n,
        "hit@3": sum(1 for x in ranks if x is not None and x <= 3) / n,
        "mrr": sum(1 / x for x in ranks if x) / n,
    }

    def correct(row: dict) -> bool:
        top = row["hits"][0] if row["hits"] else None
        passes = top is not None and top["distance"] <= threshold
        if row["expected"] is None:
            return not passes
        return passes and top["title"] in row["expected"]

    metrics["decision_accuracy"] = sum(1 for r in rows if correct(r)) / max(len(rows), 1)

    # Расстояния верных первых кандидатов и лучших кандидатов отрицательных запросов
    good = [r["hits"][0]["distance"] for r in positives if r["hits"] and r["hits"][0]["title"] in r["expected"]]
    bad = [r["hits"][0]["distance"] for r in negatives if r["hits"]]
    metrics["max_good_distance"] = max(good) if good else None
    metrics["min_negative_distance"] = min(bad) if bad else None
    if good and bad:
        metrics["recommended_threshold"] = round((max(good) + min(bad)) / 2, 3)
        metrics["thresholds_separable"] = max(good) < min(bad)
    return metrics


async def main(max_distance: float, top_k: int) -> None:
    settings = get_settings()
    threshold = max_distance if max_distance >= 0 else settings.search_max_distance
    embedder = get_embedding_model()
    rows: list[dict] = []

    async with SessionLocal() as session:
        machines = await load_machines(session)
        for item in load_yaml("queries.yaml"):
            machine = machines[item["machine"]]
            filters = SearchFilters(
                machine_kind=machine.kind, op_type=item["op_type"], dialect=machine.dialect_code
            )
            # Порог 2.0 — максимум косинусного расстояния: отсечение делаем сами
            hits = await search_operations(
                session,
                embedder,
                embedding_text(item["op_type"], item["description"]),
                filters,
                top_k,
                2.0,
            )
            rows.append(
                {
                    "id": item["id"],
                    "expected": expected_titles(item),
                    "hits": [
                        {"title": h.program_title, "distance": round(h.distance, 4)} for h in hits
                    ],
                }
            )

    metrics = compute_metrics(rows, threshold)
    print(f"Провайдер эмбеддингов: {settings.embedding_provider}, порог: {threshold}")
    print(f"{'id':36} {'ожидается':>10} {'ранг':>5} {'до ожидаемой':>13} {'до лучшей':>10}")
    for row in rows:
        titles = [h["title"] for h in row["hits"]]
        rank = rank_of(titles, row["expected"]) if row["expected"] else None
        expected_distance = row["hits"][rank - 1]["distance"] if rank else None
        best = row["hits"][0]["distance"] if row["hits"] else None
        print(
            f"{row['id']:36} {'да' if row['expected'] else 'нет':>10} {rank or '-':>5} "
            f"{expected_distance if expected_distance is not None else '-':>13} "
            f"{best if best is not None else '-':>10}"
        )
    print()
    for name, value in metrics.items():
        print(f"{name}: {value}")
    if metrics.get("thresholds_separable") is False:
        print("ВНИМАНИЕ: расстояния верных и неверных кандидатов пересекаются, порог не разделяет их")

    path = save_result(
        "search",
        settings.embedding_provider,
        {"threshold": threshold, "metrics": metrics, "rows": rows},
    )
    print(f"Результат: {path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-distance", type=float, default=-1, help="по умолчанию SEARCH_MAX_DISTANCE")
    parser.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args()
    asyncio.run(main(args.max_distance, args.top_k))
