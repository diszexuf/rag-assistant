"""Проверка связи с GigaChat: python -m app.llm.check"""

import asyncio

from app.config import get_settings
from app.llm.base import ChatMessage
from app.llm.factory import _gigachat


async def main() -> None:
    settings = get_settings()
    client = _gigachat()
    vectors = await client.embed(["наружное точение вала", "сверление отверстия"])
    print(f"эмбеддинги: {len(vectors)} шт., размерность {len(vectors[0])}")
    if len(vectors[0]) != settings.embedding_dim:
        print(f"ВНИМАНИЕ: EMBEDDING_DIM={settings.embedding_dim} не совпадает с моделью")
    answer = await client.complete(
        [ChatMessage("user", "Ответь одним словом: какой G-код задаёт быстрое перемещение?")]
    )
    print(f"чат: {answer.strip()[:200]}")
    await client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
