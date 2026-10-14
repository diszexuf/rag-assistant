import pytest

from app.kb.search import OperationHit, SearchFilters
from app.llm.base import LLMError
from app.llm.mock import DemoChatModel, HashEmbeddingModel, ScriptedChatModel
from app.nc import ConversionResult, Unconverted
from app.orchestrator.pipeline import (
    GenerationError,
    MachineContext,
    OperationRequest,
    Pipeline,
    RunProgress,
    extract_program,
    strip_comments,
)

MACHINE = MachineContext(
    name="Тестовый токарный",
    kind="lathe",
    dialect="fanuc_turn",
    max_spindle_rpm=3000,
    tools=[{"position": 1, "name": "Резец", "type": "проходной"}],
)

GOOD = "Готово.\n\n```nc\nT0101\nG97 S800 M3\nG0 X50 Z2\nG1 Z-10 F0.2\nM30\n```"
NO_END = "Готово.\n\n```nc\nT0101\nG97 S800 M3\nG1 Z-10 F0.2\n```"
TWO_ERRORS = "Готово.\n\n```nc\nT0909\nG97 S800 M3\nG1 Z-10 F0.2\n```"


def hit(dialect: str, fragment: str = "G1 Z-10 F0.2") -> OperationHit:
    return OperationHit(1, 10, "УП из базы", dialect, "turning", "точение", fragment, 0.2)


def make_search(found: dict[str, OperationHit]):
    """found: тип операции -> найденная операция. Диалект учитывается как в настоящем поиске."""
    calls: list[SearchFilters] = []

    async def search(query: str, filters: SearchFilters) -> list[OperationHit]:
        calls.append(filters)
        candidate = found.get(filters.op_type)
        if candidate is None:
            return []
        if filters.dialect and candidate.dialect != filters.dialect:
            return []
        if filters.exclude_dialect and candidate.dialect == filters.exclude_dialect:
            return []
        if filters.material:  # материал у найденных не совпадает: проверяем ослабление фильтра
            return []
        return [candidate]

    search.calls = calls
    return search


def converts_everything(text: str, src: str, dst: str) -> ConversionResult:
    return ConversionResult(text=f"(CONVERTED)\n{text}")


def converts_nothing(text: str, src: str, dst: str) -> ConversionResult:
    return ConversionResult(text=text, unconverted=[Unconverted(1, "цикл не поддерживается")])


OPS = [
    OperationRequest("turning", "точение вала"),
    OperationRequest("drilling", "сверление отверстия"),
    OperationRequest("threading", "резьба М20"),
]


async def test_three_source_kinds_and_worst_origin():
    search = make_search(
        {"turning": hit("fanuc_turn"), "drilling": hit("sinumerik_turn", "CYCLE83(1)")}
    )
    model = ScriptedChatModel([GOOD])
    stages = []

    async def on_stage(stage, attempt):
        stages.append(stage)

    pipeline = Pipeline(model, search, max_fix_attempts=3, convert=converts_everything)
    result = await pipeline.run(MACHINE, "steel", OPS, on_stage)

    assert [s.kind for s in result.sources] == ["kb", "converted", "unknown"]
    assert result.sources[1].fragment.startswith("(CONVERTED)")
    assert result.origin == "unknown"
    assert result.report.errors == [] and result.attempts == 0
    assert stages == ["searching", "converting", "generating", "validating"]
    # Материал снимается раньше диалекта
    assert [bool(f.material) for f in search.calls[:2]] == [True, False]
    prompt = model.calls[0][0].content
    assert "Образец (из базы знаний)" in prompt and "Образец (конвертирован)" in prompt
    assert "G83 X0 Z-25 Q5000" in prompt  # подсказка диалекта по типу операции
    assert "позиция 1" in prompt and "G71" in prompt


async def test_unconvertible_operation_becomes_unknown():
    search = make_search({"drilling": hit("sinumerik_turn", "CYCLE83(1)")})
    pipeline = Pipeline(ScriptedChatModel([GOOD]), search, max_fix_attempts=3, convert=converts_nothing)
    result = await pipeline.run(MACHINE, "steel", [OPS[1]])
    source = result.sources[0]
    assert source.kind == "unknown" and source.fragment == ""
    assert "sinumerik_turn" in source.note and "цикл не поддерживается" in source.note


async def test_all_from_kb_gives_kb_origin():
    search = make_search({"turning": hit("fanuc_turn")})
    result = await Pipeline(ScriptedChatModel([GOOD]), search, max_fix_attempts=3).run(
        MACHINE, "steel", [OPS[0]]
    )
    assert result.origin == "kb"


async def test_fix_loop_repairs_program():
    model = ScriptedChatModel([NO_END, GOOD])
    stages = []

    async def on_stage(stage, attempt):
        stages.append((stage, attempt))

    result = await Pipeline(model, make_search({}), max_fix_attempts=3).run(
        MACHINE, "steel", [OPS[0]], on_stage
    )
    assert result.attempts == 1 and result.report.errors == []
    assert ("fixing", 1) in stages
    fix_prompt = model.calls[1][0].content
    assert "ОТЧЁТ ВАЛИДАТОРА" in fix_prompt and "конца программы" in fix_prompt


async def test_fix_loop_stops_when_errors_do_not_decrease():
    model = ScriptedChatModel([NO_END, NO_END, GOOD])
    result = await Pipeline(model, make_search({}), max_fix_attempts=3).run(MACHINE, "steel", [OPS[0]])
    assert result.attempts == 1 and len(result.report.errors) == 1
    assert len(model.calls) == 2


async def test_fix_loop_respects_attempt_limit():
    # Каждая попытка убирает по одной ошибке, но попыток меньше, чем ошибок
    model = ScriptedChatModel([TWO_ERRORS, NO_END, GOOD])
    result = await Pipeline(model, make_search({}), max_fix_attempts=1).run(MACHINE, "steel", [OPS[0]])
    assert result.attempts == 1 and len(result.report.errors) == 1
    assert len(model.calls) == 2


async def test_model_failure_propagates_and_keeps_sources():
    progress = RunProgress()
    pipeline = Pipeline(
        ScriptedChatModel([LLMError("нет связи")]), make_search({}), max_fix_attempts=3
    )
    with pytest.raises(LLMError):
        await pipeline.run(MACHINE, "steel", [OPS[0]], progress=progress)
    assert len(progress.sources) == 1 and progress.program_text == ""


async def test_failure_during_fix_keeps_draft():
    progress = RunProgress()
    pipeline = Pipeline(
        ScriptedChatModel([NO_END, LLMError("нет связи")]), make_search({}), max_fix_attempts=3
    )
    with pytest.raises(LLMError):
        await pipeline.run(MACHINE, "steel", [OPS[0]], progress=progress)
    assert "G1 Z-10" in progress.program_text and len(progress.report.errors) == 1


async def test_answer_without_program_is_an_error():
    pipeline = Pipeline(ScriptedChatModel(["Не могу помочь."]), make_search({}), max_fix_attempts=3)
    with pytest.raises(GenerationError):
        await pipeline.run(MACHINE, "steel", [OPS[0]])


def test_extract_program():
    assert extract_program("Пояснение.\n```nc\nG0 X1\nM30\n```\nКонец.") == (
        "G0 X1\nM30",
        "Пояснение.\n\nКонец.",
    )
    assert extract_program("нет программы") is None
    assert extract_program("```nc\n\n```") is None
    # Из нескольких блоков берётся самый полный
    assert extract_program("```nc\nG0 X1\n```\n```nc\nG0 X1\nM30\n```")[0] == "G0 X1\nM30"


def test_strip_comments_removes_foreign_comment_syntax():
    text = "% start\nG0 X1 ; podvod\n(OK)\n\nM30 % end"
    assert strip_comments(text, "fanuc_turn") == "G0 X1\n(OK)\nM30"
    assert strip_comments("G0 X1 ; podvod", "sinumerik_turn") == "G0 X1 ; podvod"


async def test_demo_model_assembles_program_from_sources():
    search = make_search({"turning": hit("fanuc_turn", "T0101\nG97 S800 M3\nG1 Z-10 F0.2")})
    result = await Pipeline(DemoChatModel(), search, max_fix_attempts=3).run(
        MACHINE, "steel", OPS[:2]
    )
    assert "G1 Z-10 F0.2" in result.program_text
    assert "(операция без источника)" in result.program_text
    assert result.program_text.endswith("M30") and result.report.errors == []


async def test_hash_embeddings_are_deterministic_and_reflect_word_overlap():
    model = HashEmbeddingModel(256)
    a, b, c = await model.embed(
        ["Подрезка торца заготовки резцом", "Подрезка торца заготовки", "Нарезание резьбы М20"]
    )
    cosine = lambda x, y: sum(p * q for p, q in zip(x, y))
    assert a == (await model.embed(["Подрезка торца заготовки резцом"]))[0]
    assert cosine(a, b) > 0.8 > 0.2 > cosine(a, c)


async def test_missing_request_value_is_warning_and_does_not_start_fix_loop():
    model = ScriptedChatModel([GOOD])  # в GOOD глубина Z-10, в запросе 30
    operation = OperationRequest("drilling", "Сверление на глубину 30 мм резцом T0101")
    result = await Pipeline(model, make_search({}), max_fix_attempts=3).run(MACHINE, "steel", [operation])
    codes = [(i.code, i.severity) for i in result.report.issues]
    assert codes == [("REQUEST_VALUE_MISSING", "warning")]
    assert result.report.errors == [] and result.attempts == 0 and len(model.calls) == 1
