from dataclasses import dataclass, field
from typing import Literal

Severity = Literal["error", "warning"]


@dataclass(frozen=True)
class Word:
    """Адрес со значением: X10.5, S1200, LIMS=1300."""

    name: str
    value: str

    def as_float(self) -> float | None:
        try:
            return float(self.value)
        except ValueError:
            return None


@dataclass
class Block:
    line: int
    raw: str
    number: int | None = None
    words: list[Word] = field(default_factory=list)
    # Нормализованные коды команд: G1, M30, CYCLE95
    commands: list[str] = field(default_factory=list)
    # Имена, которых нет в описании диалекта: подпрограммы, метки и т. п.
    unknown_names: list[str] = field(default_factory=list)
    comment: str = ""
    error: str | None = None

    def word(self, name: str) -> Word | None:
        return next((w for w in self.words if w.name == name), None)


@dataclass
class ParsedProgram:
    dialect: str
    blocks: list[Block]


@dataclass(frozen=True)
class Issue:
    line: int
    code: str
    severity: Severity
    message: str


@dataclass
class ValidationReport:
    issues: list[Issue] = field(default_factory=list)

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "error"]

    def to_dict(self) -> dict:
        return {
            "issues": [
                {"line": i.line, "code": i.code, "severity": i.severity, "message": i.message}
                for i in self.issues
            ],
            "error_count": len(self.errors),
        }


@dataclass(frozen=True)
class MachineInfo:
    """Сведения о станке, нужные валидатору."""

    max_spindle_rpm: int | None = None
    tool_positions: frozenset[int] | None = None


@dataclass(frozen=True)
class Unconverted:
    line: int
    reason: str


@dataclass
class ConversionResult:
    text: str
    unconverted: list[Unconverted] = field(default_factory=list)
