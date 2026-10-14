import hashlib
import math
import re

from app.llm.base import ChatMessage, LLMError

_FENCE = re.compile(r"```[a-z]*\n(.*?)```", re.DOTALL)
_TOKEN = re.compile(r"[a-zа-яё0-9]+")


class ScriptedChatModel:
    """Отдаёт заранее заданные ответы по очереди. Исключение в очереди будет выброшено."""

    def __init__(self, responses: list[str | Exception]):
        self._responses = list(responses)
        self.calls: list[list[ChatMessage]] = []

    async def complete(self, messages: list[ChatMessage], **params) -> str:
        self.calls.append(messages)
        if not self._responses:
            raise LLMError("У сценарной заглушки закончились ответы")
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class DemoChatModel:
    """Собирает УП из фрагментов-источников, найденных в промпте.

    Нужна, чтобы прототип показывал осмысленный результат без обращения к API.
    Опирается на разметку шаблонов промптов: строку «Диалект: <код>» и блоки ```nc.
    """

    async def complete(self, messages: list[ChatMessage], **params) -> str:
        prompt = messages[-1].content
        semicolon = "sinumerik" in _field(prompt, "Диалект")
        fragments = [f.strip("\n") for f in _FENCE.findall(prompt)]
        # Последний блок в шаблонах — образец формата ответа, а не источник
        fragments = [f for f in fragments if f.strip() != "текст программы"]

        if "ИНСТРУКЦИЯ ТЕХНОЛОГА" in prompt:
            # Режим правки: первый блок — текущая программа, остальные — образцы новых операций
            current, new = (fragments[0] if fragments else ""), fragments[1:]
            note = "; операция без источника" if semicolon else "(операция без источника)"
            lines = current.splitlines()
            added = [f or note for f in new]
            program = "\n".join(lines[:-1] + added + lines[-1:]) if added else current
            explanation = "Заглушка: инструкция не применена, добавлены фрагменты новых операций."
        elif "ОТЧЁТ ВАЛИДАТОРА" in prompt:
            program = fragments[-1] if fragments else ""
            explanation = "Заглушка: добавлен конец программы."
        else:
            note = "; операция без источника" if semicolon else "(операция без источника)"
            program = "\n".join(f or note for f in fragments)
            explanation = "Заглушка: программа собрана из найденных фрагментов без изменений."

        if not re.search(r"\bM(30|0?2)\b", program.upper()):
            program = f"{program}\nM30".strip()
        return f"{explanation}\n\n```nc\n{program}\n```"


def _field(prompt: str, name: str) -> str:
    match = re.search(rf"^{name}:\s*(.+)$", prompt, re.MULTILINE)
    return match.group(1).strip().lower() if match else ""


class HashEmbeddingModel:
    """Детерминированные эмбеддинги на хешах слов: близость — по совпадению слов."""

    def __init__(self, dim: int):
        self.dim = dim

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(text) for text in texts]

    def _embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dim
        for token in _TOKEN.findall(text.lower()):
            if len(token) < 3 and not token.isdigit():
                continue
            # Грубое отсечение окончаний, чтобы «точение» и «точения» совпадали
            stem = token if token.isdigit() else token[:5]
            digest = hashlib.md5(stem.encode("utf-8")).digest()
            vector[int.from_bytes(digest[:4], "big") % self.dim] += 1.0
        norm = math.sqrt(sum(v * v for v in vector))
        if norm == 0:
            vector[0] = 1.0
            return vector
        return [v / norm for v in vector]
