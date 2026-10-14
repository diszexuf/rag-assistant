from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml

DIALECTS_DIR = Path(__file__).parent / "dialects"


@dataclass(frozen=True)
class Dialect:
    code: str
    title: str
    syntax: Literal["fanuc", "sinumerik"]
    commands: dict[str, str]
    program_end: tuple[str, ...]
    css_on: str
    css_off: str
    # Команда ограничения оборотов (Fanuc: G50 S...) либо имя адреса (Sinumerik: LIMS=...)
    spindle_limit_command: str | None
    spindle_limit_word: str | None
    # fanuc_tt: T0101 — первые цифры номер инструмента, последние две корректор; plain: T1
    tool_format: Literal["fanuc_tt", "plain"]
    # Формулировка для промпта: как оформляются комментарии
    comment_syntax: str = ""
    # Тип операции -> чем она выполняется в этом диалекте
    op_hints: dict[str, str] | None = None


class UnknownDialect(ValueError):
    pass


@lru_cache
def load_dialect(code: str) -> Dialect:
    path = DIALECTS_DIR / f"{code}.yaml"
    if not path.exists():
        raise UnknownDialect(f"Нет описания диалекта: {code}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    spindle = data.get("spindle", {})
    return Dialect(
        code=code,
        title=data["title"],
        syntax=data["syntax"],
        commands={str(k): str(v) for k, v in data["commands"].items()},
        program_end=tuple(data["program_end"]),
        css_on=spindle.get("css_on", "G96"),
        css_off=spindle.get("css_off", "G97"),
        spindle_limit_command=spindle.get("limit_command"),
        spindle_limit_word=spindle.get("limit_word"),
        tool_format=data.get("tool_format", "plain"),
        comment_syntax=data.get("comment_syntax", ""),
        op_hints=data.get("op_hints"),
    )


def list_dialects() -> list[str]:
    return sorted(p.stem for p in DIALECTS_DIR.glob("*.yaml"))
