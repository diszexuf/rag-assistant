"""Проверка эмбеддингов: python -m app.llm.check_embeddings

Печатает размерность и близость трёх пар текстов: близкие по смыслу, одного типа
операций разными словами и разных типов операций.
"""

import asyncio
import math

from app.config import get_settings
from app.llm.factory import get_embedding_model

PAIRS = [
    (
        "близкие формулировки",
        "Подрезка торца заготовки диаметром 60 мм резцом T0101",
        "Торцевание заготовки Ø60 мм, резец T0101",
    ),
    (
        "одна операция разными словами",
        "Сверление центрального отверстия диаметром 20 мм на глубину 30 мм",
        "Просверлить сквозное отверстие Ø20 в заготовке втулки",
    ),
    (
        "разные операции",
        "Подрезка торца заготовки диаметром 60 мм резцом T0101",
        "Нарезание наружной метрической резьбы М30х1.5 длиной 20 мм",
    ),
]


def cosine_distance(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return 1 - dot / norm if norm else 1.0


async def main() -> None:
    settings = get_settings()
    embedder = get_embedding_model()
    print(f"EMBEDDING_PROVIDER={settings.embedding_provider}")
    vectors = await embedder.embed([text for _, a, b in PAIRS for text in (a, b)])
    print(f"размерность: {len(vectors[0])}")
    if len(vectors[0]) != settings.embedding_dim:
        print(f"ВНИМАНИЕ: EMBEDDING_DIM={settings.embedding_dim} не совпадает с моделью")
    for index, (title, _, _) in enumerate(PAIRS):
        distance = cosine_distance(vectors[2 * index], vectors[2 * index + 1])
        print(f"{title}: косинусное расстояние {distance:.3f}")


if __name__ == "__main__":
    asyncio.run(main())
