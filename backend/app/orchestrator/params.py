"""Проверка того, что числа из описания операции попали в программу.

Модель нередко оставляет в программе значения из образца вместо значений запроса.
Проверка мягкая: даёт предупреждения, а не ошибки, и не запускает цикл доработки.
Модуль зависит только от app.nc.
"""

import re
from dataclasses import dataclass

from app import nc
from app.nc import Issue

_TOLERANCE = 1e-6
_NUM = r"(\d+(?:[.,]\d+)?)"
_DIAMETER_SIGNS = "Øø⌀"

# kind -> регулярные выражения; значение — первая группа. Регистр не учитывается.
_COMMON_PATTERNS: dict[str, list[str]] = {
    "cut_depth": [rf"глубин\w*\s+резани\w*\s+{_NUM}"],
    "allowance": [rf"припуск\w*\s+{_NUM}"],
    "speed": [rf"{_NUM}\s*м/мин", rf"скорост\w*\s+(?:резания\s+)?{_NUM}"],
    "rpm": [rf"{_NUM}\s*об/мин", rf"частот\w*\s+вращения\s+{_NUM}"],
    "feed": [rf"подач\w*(?:\s+\w+){{0,3}}?\s+{_NUM}", rf"{_NUM}\s*мм/об"],
    "tool": [r"(?<![\w.])[TТ]\s?(\d{1,4})(?!\d)"],
}

_TYPE_PATTERNS: dict[str, dict[str, list[str]]] = {
    "facing": {"diameter": [rf"(?:диаметр\w*|[{_DIAMETER_SIGNS}])\s*{_NUM}"]},
    "turning": {
        "final_diameter": [rf"до\s+(?:диаметра\s+)?{_NUM}\s*мм"],
        "length": [rf"длин\w*\s+{_NUM}"],
    },
    "drilling": {"depth": [rf"глубин\w*\s+(?!резани)(?:сверления\s+)?{_NUM}"]},
    "threading": {
        "length": [rf"длин\w*\s+{_NUM}"],
        "pitch": [rf"[МM]\d+(?:[.,]\d+)?\s*[хx×]\s*{_NUM}"],
    },
}

_KIND_TITLES = {
    "diameter": "диаметр",
    "final_diameter": "конечный диаметр",
    "length": "длина",
    "depth": "глубина",
    "cut_depth": "глубина резания",
    "allowance": "припуск",
    "speed": "скорость резания",
    "rpm": "частота вращения",
    "feed": "подача",
    "tool": "инструмент",
    "pitch": "шаг резьбы",
}


@dataclass(frozen=True)
class ExpectedValue:
    operation: int  # номер операции в запросе, с 1
    kind: str
    value: float
    text: str  # фрагмент описания, из которого значение извлечено


@dataclass
class ProgramValues:
    """Числа программы: по адресам, из аргументов циклов и номера инструмента."""

    by_address: dict[str, list[float]]
    cycle_args: list[float]
    tool_numbers: set[int]
    tool_positions: set[int]

    def all_values(self) -> list[float]:
        return [v for values in self.by_address.values() for v in values] + self.cycle_args

    def address_or_args(self, *addresses: str) -> list[float]:
        found = [v for a in addresses for v in self.by_address.get(a, [])]
        return found + self.cycle_args


def _to_float(raw: str) -> float:
    return float(raw.replace(",", "."))


def extract_values(op_type: str, description: str, operation: int) -> list[ExpectedValue]:
    """Значения из описания. Пустой op_type — свободная инструкция: только общие виды."""
    patterns = dict(_COMMON_PATTERNS)
    patterns.update(_TYPE_PATTERNS.get(op_type, {}))
    found: list[ExpectedValue] = []
    seen: set[tuple[str, float]] = set()
    for kind, expressions in patterns.items():
        for expression in expressions:
            for match in re.finditer(expression, description, re.IGNORECASE):
                value = _to_float(match.group(1))
                if (kind, value) in seen:
                    continue
                seen.add((kind, value))
                found.append(ExpectedValue(operation, kind, value, match.group(0).strip()))
    return found


def program_values(text: str, dialect: str) -> ProgramValues:
    spec = nc.load_dialect(dialect)
    values = ProgramValues({}, [], set(), set())
    for block in nc.parse(text, dialect).blocks:
        for word in block.words:
            if word.name.endswith("()"):
                for part in word.value.split(","):
                    try:
                        values.cycle_args.append(float(part.strip()))
                    except ValueError:
                        continue
                continue
            number = word.as_float()
            if number is None:
                continue
            values.by_address.setdefault(word.name, []).append(number)
            if word.name == "T":
                digits = word.value.lstrip("+-").split(".")[0]
                if digits.isdigit():
                    values.tool_numbers.add(int(digits))
                    if spec.tool_format == "fanuc_tt" and len(digits) >= 3:
                        values.tool_positions.add(int(digits[:-2]))
                    else:
                        values.tool_positions.add(int(digits))
    return values


def _has(candidates: list[float], target: float, *, absolute: bool = True) -> bool:
    return any(
        abs((abs(v) if absolute else v) - target) <= _TOLERANCE for v in candidates
    )


def _is_present(expected: ExpectedValue, program: ProgramValues) -> bool:
    kind, value = expected.kind, expected.value
    if kind == "diameter":
        # Подвод к торцу идёт с запасом на несколько миллиметров
        return any(value - _TOLERANCE <= abs(x) <= value + 5 for x in program.by_address.get("X", []))
    if kind == "final_diameter":
        return _has(program.address_or_args("X"), value)
    if kind in ("length", "depth"):
        return _has(program.address_or_args("Z"), value)
    if kind in ("cut_depth", "allowance"):
        return _has(program.all_values(), value)
    if kind in ("speed", "rpm"):
        return _has(program.address_or_args("S"), value)
    if kind in ("feed", "pitch"):
        return _has(program.address_or_args("F"), value)
    if kind == "tool":
        number = int(value)
        return number in program.tool_numbers or (number < 100 and number in program.tool_positions)
    return True


def missing_value_issues(
    text: str, dialect: str, operations: list[tuple[str, str]], first_index: int = 1
) -> list[Issue]:
    """Предупреждения о значениях описаний, которых нет в программе.

    operations — пары (тип операции, описание); пустой тип означает свободную инструкцию.
    Проверка идёт по всей программе: границ операций в сгенерированном тексте нет.
    """
    program = program_values(text, dialect)
    issues: list[Issue] = []
    for offset, (op_type, description) in enumerate(operations):
        index = first_index + offset
        label = "Инструкция" if not op_type else f"Операция {index}"
        for expected in extract_values(op_type, description, index):
            if _is_present(expected, program):
                continue
            issues.append(
                Issue(
                    0,
                    "REQUEST_VALUE_MISSING",
                    "warning",
                    f"{label}: в программе нет значения «{expected.text}» "
                    f"({_KIND_TITLES[expected.kind]} {expected.value:g})",
                )
            )
    return issues
