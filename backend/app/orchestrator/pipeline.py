import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from app import nc
from app.kb.ingest import embedding_text
from app.kb.refs import CUTTING_MODES, MATERIALS, OP_TYPES
from app.kb.search import OperationHit, SearchFilters
from app.llm.base import ChatMessage, ChatModel
from app.nc import ConversionResult, MachineInfo, ValidationReport
from app.orchestrator.params import missing_value_issues

PROMPTS_DIR = Path(__file__).parent / "prompts"
_FENCE = re.compile(r"```[a-zA-Z]*\n(.*?)```", re.DOTALL)

SourceKind = Literal["kb", "converted", "unknown"]
# Чем правее, тем слабее опора на базу знаний
_ORIGIN_RANK: dict[str, int] = {"kb": 0, "converted": 1, "unknown": 2}

SearchFn = Callable[[str, SearchFilters], Awaitable[list[OperationHit]]]
StageFn = Callable[[str, int], Awaitable[None]]
ConvertFn = Callable[[str, str, str], ConversionResult]
ValidateFn = Callable[[str, str, MachineInfo | None], ValidationReport]


class GenerationError(Exception):
    """Генерация не дала результата по причине, не связанной с доступностью модели."""


@dataclass(frozen=True)
class OperationRequest:
    op_type: str
    description: str


@dataclass(frozen=True)
class MachineContext:
    name: str
    kind: str
    dialect: str
    max_spindle_rpm: int
    tools: list[dict]
    notes: str = ""

    @property
    def info(self) -> MachineInfo:
        return MachineInfo(
            max_spindle_rpm=self.max_spindle_rpm,
            tool_positions=frozenset(int(t["position"]) for t in self.tools),
        )


@dataclass
class OperationSource:
    index: int
    op_type: str
    description: str
    kind: SourceKind
    fragment: str = ""
    program_id: int | None = None
    program_title: str | None = None
    operation_id: int | None = None
    src_dialect: str | None = None
    distance: float | None = None
    note: str = ""

    def to_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass
class RunProgress:
    """Промежуточное состояние: позволяет сохранить черновик, если запуск оборвался."""

    sources: list[OperationSource] = field(default_factory=list)
    # Пары (тип, описание) запроса: по ним проверяется наличие чисел в программе
    operations: list[tuple[str, str]] = field(default_factory=list)
    program_text: str = ""
    explanation: str = ""
    report: ValidationReport | None = None
    attempts: int = 0


@dataclass
class GenerationResult:
    program_text: str
    explanation: str
    origin: SourceKind
    report: ValidationReport
    sources: list[OperationSource]
    attempts: int


def worst_origin(sources: list[OperationSource]) -> SourceKind:
    return max((s.kind for s in sources), key=_ORIGIN_RANK.__getitem__, default="unknown")


def extract_program(response: str) -> tuple[str, str] | None:
    """Возвращает (текст УП, пояснение) или None, если блока с программой нет."""
    # Модель иногда даёт черновик и итог в разных блоках — берём самый полный
    program = max(_FENCE.findall(response), key=len, default="").strip("\n")
    if not program.strip():
        return None
    return program, _FENCE.sub("", response).strip()


def strip_comments(program: str, dialect: str) -> str:
    """Убирает комментарии, которые модель добавляет вопреки промпту и в чужом синтаксисе."""
    markers = ["%", "//", "--"] + ([";"] if nc.load_dialect(dialect).syntax == "fanuc" else [])
    lines = []
    for line in program.splitlines():
        for marker in markers:
            line = line.split(marker, 1)[0]
        if line.strip():
            lines.append(line.rstrip())
    return "\n".join(lines)


async def _no_stage(stage: str, attempt: int) -> None:
    return None


class Pipeline:
    def __init__(
        self,
        chat_model: ChatModel,
        search: SearchFn,
        *,
        max_fix_attempts: int,
        convert: ConvertFn = nc.convert,
        validate: ValidateFn = nc.validate,
    ):
        self._chat = chat_model
        self._search = search
        self._max_fix_attempts = max_fix_attempts
        self._convert = convert
        self._validate = validate
        # В системном сообщении нет подстановок: рендерим один раз
        self._system = _render("system.md")

    async def run(
        self,
        machine: MachineContext,
        material: str,
        operations: list[OperationRequest],
        on_stage: StageFn = _no_stage,
        progress: RunProgress | None = None,
    ) -> GenerationResult:
        progress = progress if progress is not None else RunProgress()
        progress.operations = [(o.op_type, o.description) for o in operations]

        await on_stage("searching", 0)
        for index, operation in enumerate(operations, start=1):
            source = await self._resolve_source(index, operation, machine, material, on_stage)
            progress.sources.append(source)

        await on_stage("generating", 0)
        prompt = self._generation_prompt(machine, material, progress.sources)
        program, progress.explanation = await self._ask_program(prompt, machine.dialect, required=True)
        progress.program_text = program

        await on_stage("validating", 0)
        report = self._check(machine, program, progress.operations)
        progress.report = report

        while report.errors and progress.attempts < self._max_fix_attempts:
            progress.attempts += 1
            await on_stage("fixing", progress.attempts)
            answer = await self._ask_program(self._fix_prompt(machine, program, report), machine.dialect)
            if answer is None:
                break
            candidate, explanation = answer
            await on_stage("validating", progress.attempts)
            candidate_report = self._check(machine, candidate, progress.operations)
            # Досрочная остановка: попытка не уменьшила число ошибок
            if len(candidate_report.errors) >= len(report.errors):
                break
            program, report = candidate, candidate_report
            progress.program_text, progress.report = program, report
            progress.explanation = explanation or progress.explanation

        return GenerationResult(
            program_text=program,
            explanation=progress.explanation,
            origin=worst_origin(progress.sources),
            report=report,
            sources=progress.sources,
            attempts=progress.attempts,
        )

    def _check(
        self, machine: MachineContext, program: str, operations: list[tuple[str, str]]
    ) -> ValidationReport:
        """Отчёт валидатора плюс предупреждения о числах запроса, которых нет в программе."""
        report = self._validate(program, machine.dialect, machine.info)
        report.issues.extend(missing_value_issues(program, machine.dialect, operations))
        report.issues.sort(key=lambda issue: issue.line)
        return report

    async def _resolve_source(
        self,
        index: int,
        operation: OperationRequest,
        machine: MachineContext,
        material: str,
        on_stage: StageFn,
    ) -> OperationSource:
        query = embedding_text(operation.op_type, operation.description)
        source = OperationSource(index, operation.op_type, operation.description, "unknown")

        def base(**kwargs) -> SearchFilters:
            return SearchFilters(machine_kind=machine.kind, op_type=operation.op_type, **kwargs)

        # Ослабление фильтра: сначала снимается материал, затем диалект
        for filters in (base(dialect=machine.dialect, material=material), base(dialect=machine.dialect)):
            hits = await self._search(query, filters)
            if hits:
                return _with_hit(source, hits[0], "kb", hits[0].fragment)

        for filters in (
            base(exclude_dialect=machine.dialect, material=material),
            base(exclude_dialect=machine.dialect),
        ):
            hits = await self._search(query, filters)
            if not hits:
                continue
            hit = hits[0]
            await on_stage("converting", 0)
            result = self._convert(hit.fragment, hit.dialect, machine.dialect)
            if result.unconverted:
                reasons = sorted({u.reason for u in result.unconverted})
                source.note = (
                    f"Найдена похожая операция на диалекте {hit.dialect} "
                    f"(«{hit.program_title}»), но перевести её не удалось: {'; '.join(reasons)}"
                )
                return source
            return _with_hit(source, hit, "converted", result.text)

        source.note = "В базе знаний нет похожих операций"
        return source

    async def _ask_program(
        self, prompt: str, dialect: str, required: bool = False
    ) -> tuple[str, str] | None:
        response = await self._chat.complete(
            [ChatMessage("system", self._system), ChatMessage("user", prompt)]
        )
        answer = extract_program(response)
        if answer:
            program = strip_comments(answer[0], dialect)
            answer = (program, answer[1]) if program else None
        if answer is None and required:
            raise GenerationError("Модель не вернула текст программы")
        return answer

    def _generation_prompt(
        self, machine: MachineContext, material: str, sources: list[OperationSource]
    ) -> str:
        return _render(
            "generate.md",
            **_machine_fields(machine),
            material=MATERIALS.get(material, material),
            machine_notes=machine.notes or "нет",
            cutting_modes=_cutting_modes(material, sources),
            operations="\n\n".join(_operation_block(s, machine.dialect) for s in sources),
        )

    def _fix_prompt(self, machine: MachineContext, program: str, report: ValidationReport) -> str:
        lines = [f"- строка {i.line}: {i.message}" for i in report.errors]
        return _render(
            "fix.md",
            **_machine_fields(machine),
            report="\n".join(lines),
            program=program,
        )


def _with_hit(
    source: OperationSource, hit: OperationHit, kind: SourceKind, fragment: str
) -> OperationSource:
    source.kind = kind
    source.fragment = fragment
    source.program_id = hit.program_id
    source.program_title = hit.program_title
    source.operation_id = hit.operation_id
    source.src_dialect = hit.dialect
    source.distance = round(hit.distance, 4)
    return source


_KIND_TITLES: dict[str, str] = {
    "kb": "из базы знаний",
    "converted": "конвертирован",
    "unknown": "источника нет",
}


def _operation_block(source: OperationSource, dialect: str) -> str:
    title = OP_TYPES.get(source.op_type, source.op_type)
    hint = (nc.load_dialect(dialect).op_hints or {}).get(source.op_type, "нет данных")
    head = (
        f"Операция {source.index}: {title}\n"
        f"Описание (отсюда брать размеры и режимы): {source.description}\n"
        f"Как выполняется в этом диалекте: {hint}\n"
        f"Образец ({_KIND_TITLES[source.kind]}):"
    )
    # Пустой блок сохраняет позицию операции для демонстрационной заглушки
    return f"{head}\n```nc\n{source.fragment}\n```"


def _cutting_modes(material: str, sources: list[OperationSource]) -> str:
    """Режимы резания для материала — только для типов операций из запроса."""
    modes = CUTTING_MODES.get(material, {})
    lines, seen = [], set()
    for source in sources:
        if source.op_type in seen or source.op_type not in modes:
            continue
        seen.add(source.op_type)
        lines.append(f"- {OP_TYPES.get(source.op_type, source.op_type)}: {modes[source.op_type]}")
    return "\n".join(lines) or "- нет данных"


def _machine_fields(machine: MachineContext) -> dict[str, str]:
    dialect = nc.load_dialect(machine.dialect)
    tools = "\n".join(
        f"- позиция {t['position']}: {t.get('name', '')} ({t.get('type', '')})" for t in machine.tools
    )
    commands = "\n".join(f"- {code}: {title}" for code, title in dialect.commands.items())
    return {
        "machine_name": machine.name,
        "dialect_code": dialect.code,
        "dialect_title": dialect.title,
        "max_spindle_rpm": str(machine.max_spindle_rpm),
        "tools": tools or "- нет данных",
        "commands": commands,
        "comment_syntax": dialect.comment_syntax,
    }


def _render(template: str, **fields: str) -> str:
    text = (PROMPTS_DIR / template).read_text(encoding="utf-8")
    for name, value in fields.items():
        text = text.replace("{{" + name + "}}", value)
    return text
