"""Нарезка готовой УП на операции для загрузки в базу знаний.

Чистые функции без БД и модели: границы операций, тип операции и признаки для описания.
Результат — черновик, который проверяет человек (см. seed/ingest.py).
"""

from dataclasses import dataclass, field

from app import nc
from app.kb.refs import OP_TYPES
from app.nc.types import Block

# Команды, по которым определяется тип операции, в порядке приоритета
_CYCLES: list[tuple[str, frozenset[str]]] = [
    ("threading", frozenset({"G76", "G92", "G32", "G33", "CYCLE97"})),
    ("drilling", frozenset({"G74", "G81", "G82", "G83", "CYCLE81", "CYCLE82", "CYCLE83"})),
    ("turning", frozenset({"G70", "G71", "G73", "G90", "CYCLE95"})),
    ("facing", frozenset({"G94"})),
]
# Кадры, которые бывают только в концовке программы
_FOOTER_COMMANDS = frozenset({"M5", "M9", "M30", "M2", "M17", "M99", "RET"})


@dataclass
class OperationDraft:
    op_type: str | None  # None — тип определить не удалось
    line_start: int  # номера строк исходного текста, с 1
    line_end: int
    features: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


@dataclass
class SplitResult:
    header: tuple[int, int] | None
    operations: list[OperationDraft]
    footer: tuple[int, int] | None
    warnings: list[str] = field(default_factory=list)


def split_program(text: str, dialect: str) -> SplitResult:
    lines = text.splitlines()
    spec = nc.load_dialect(dialect)
    blocks = nc.parse(text, dialect).blocks
    result = SplitResult(header=None, operations=[], footer=None)
    if not blocks:
        result.warnings.append("В программе нет ни одного кадра")
        return result

    for block in blocks:
        if block.error:
            result.warnings.append(f"Строка {block.line}: {block.error}")

    footer_commands = _FOOTER_COMMANDS | set(spec.program_end)
    footer_from = _footer_start(blocks, footer_commands)
    body = [b for b in blocks if footer_from is None or b.line < footer_from]
    if footer_from is not None:
        result.footer = (footer_from, _last_line(lines))

    segments = _segments(body)
    if not segments:
        result.warnings.append("В программе нет вызова инструмента: вся программа принята за одну операцию")
        segments = [(body[0].line, body)] if body else []
    elif segments[0][0] > body[0].line:
        result.header = (1, _trim_end(lines, segments[0][0] - 1, 1))

    boundary_after_last = (footer_from - 1) if footer_from is not None else _last_line(lines)
    for index, (start, seg_blocks) in enumerate(segments):
        end = segments[index + 1][0] - 1 if index + 1 < len(segments) else boundary_after_last
        end = _trim_end(lines, end, start)
        draft = OperationDraft(None, start, end)
        draft.features = _features(seg_blocks, spec)
        draft.op_type, draft.warnings = _classify(seg_blocks)
        if draft.op_type is None:
            draft.warnings.append("Тип операции определить не удалось")
        result.operations.append(draft)
    return result


def _footer_start(blocks: list[Block], footer_commands: set[str]) -> int | None:
    start = None
    for block in reversed(blocks):
        if block.error or block.words or block.unknown_names or not block.commands:
            break
        if not set(block.commands) <= footer_commands:
            break
        start = block.line
    return start


def _has_cut(block: Block) -> bool:
    """Кадр с рабочим перемещением или циклом: после него новый вызов инструмента — новая операция."""
    cutting = {"G1", "G2", "G3"}.union(*(commands for _, commands in _CYCLES))
    called = {w.name.removesuffix("()") for w in block.words if w.name.endswith("()")}
    return bool((_names(block) | called) & cutting)


def _segments(blocks: list[Block]) -> list[tuple[int, list[Block]]]:
    """Операция начинается с вызова инструмента.

    Вызов инструмента до первого рабочего перемещения (повтор, уточнение) операцию не делит.
    """
    segments: list[tuple[int, list[Block]]] = []
    has_cut = False
    for block in blocks:
        if block.word("T") is not None and (not segments or has_cut):
            segments.append((block.line, []))
            has_cut = False
        if segments:
            segments[-1][1].append(block)
            has_cut = has_cut or _has_cut(block)
    return segments


def _last_line(lines: list[str]) -> int:
    for number in range(len(lines), 0, -1):
        if lines[number - 1].strip():
            return number
    return 1


def _trim_end(lines: list[str], end: int, start: int) -> int:
    while end > start and not lines[end - 1].strip():
        end -= 1
    return end


def _names(block: Block) -> set[str]:
    return set(block.commands) | set(block.unknown_names)


def _classify(blocks: list[Block]) -> tuple[str | None, list[str]]:
    names: set[str] = set()
    for block in blocks:
        names |= _names(block)
    # Вызов цикла Sinumerik попадает в слова под именем CYCLE95()
    for block in blocks:
        names |= {w.name.removesuffix("()") for w in block.words if w.name.endswith("()")}

    found = [op_type for op_type, commands in _CYCLES if names & commands]
    warnings = []
    if len(found) > 1:
        warnings.append(
            "В операции признаки нескольких типов: "
            + ", ".join(OP_TYPES[t] for t in found)
            + ". Проверьте разбиение"
        )
    if found:
        return found[0], warnings

    # Без циклов: по характеру линейных перемещений
    facing = turning = False
    for block in blocks:
        if "G1" not in block.commands:
            continue
        x, z = block.word("X"), block.word("Z")
        if x is not None and z is None and (value := x.as_float()) is not None and value <= 0:
            facing = True
        if z is not None and (value := z.as_float()) is not None and abs(value) > 1:
            turning = True
    if facing and turning:
        warnings.append("В операции и подрезка торца, и точение. Проверьте разбиение")
    if facing and not turning:
        return "facing", warnings
    if turning:
        return "turning", warnings
    return None, warnings


def _features(blocks: list[Block], spec: nc.Dialect) -> dict:
    tool = next((b.word("T").value for b in blocks if b.word("T")), None)
    commands: list[str] = []
    cycles: list[str] = []
    x_values: list[float] = []
    z_values: list[float] = []
    speeds: list[dict] = []
    feeds: list[float] = []
    css = False
    for block in blocks:
        for name in sorted(_names(block)):
            if name not in commands:
                commands.append(name)
        for word in block.words:
            if word.name.endswith("()") and word.name not in cycles:
                cycles.append(word.name.removesuffix("()"))
        if spec.css_on in block.commands:
            css = True
        if spec.css_off in block.commands:
            css = False
        for name, values in (("X", x_values), ("Z", z_values), ("F", feeds)):
            word = block.word(name)
            if word is not None and (value := word.as_float()) is not None:
                values.append(value)
        speed = block.word("S")
        # G50 S... — ограничение оборотов, а не режим
        if speed is not None and spec.spindle_limit_command not in block.commands:
            if (value := speed.as_float()) is not None:
                speeds.append({"value": value, "css": css})
    for code in ("G71", "G70", "G73", "G76", "G83", "G81", "G82", "G92", "G90", "G94"):
        if code in commands and code not in cycles:
            cycles.append(code)
    return {
        "tool": tool,
        "commands": commands,
        "cycles": cycles,
        "x": x_values,
        "z": z_values,
        "speeds": speeds,
        "feeds": feeds,
    }


def _number(value: float) -> str:
    return f"{value:g}"


def template_description(op_type: str, features: dict) -> str:
    """Описание по признакам, без модели. Содержит числа, чтобы поиск различал операции."""
    parts = []
    if features.get("tool"):
        parts.append(f"инструмент T{features['tool']}")
    if features.get("cycles"):
        label = "цикл" if len(features["cycles"]) == 1 else "циклы"
        parts.append(f"{label} {', '.join(features['cycles'])}")
    for name, values in (("X", features.get("x")), ("Z", features.get("z"))):
        if values:
            if values[0] == values[-1]:
                parts.append(f"{name} {_number(values[0])}")
            else:
                parts.append(f"{name} от {_number(values[0])} до {_number(values[-1])}")
    if features.get("speeds"):
        speed = features["speeds"][0]
        if speed["css"]:
            parts.append(f"скорость резания {_number(speed['value'])} м/мин (G96)")
        else:
            parts.append(f"частота вращения {_number(speed['value'])} об/мин (G97)")
    if features.get("feeds"):
        parts.append(f"подача {_number(features['feeds'][0])} мм/об")
    title = OP_TYPES.get(op_type, op_type)
    return f"{title}: {', '.join(parts)}" if parts else title
