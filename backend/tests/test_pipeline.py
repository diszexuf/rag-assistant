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
    prompt = model.calls[0][-1].content
    assert "Образец (из базы знаний)" in prompt and "Образец (конвертирован)" in prompt
    assert "G83 X0 Z-<глубина отверстия>" in prompt  # подсказка диалекта по типу операции
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
    fix_prompt = model.calls[1][-1].content
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


async def test_system_message_and_cutting_modes_in_prompt():
    model = ScriptedChatModel([GOOD])
    ops = [OperationRequest("facing", "подрезка торца"), OperationRequest("drilling", "сверление")]
    await Pipeline(model, make_search({}), max_fix_attempts=3).run(MACHINE, "aluminium", ops)
    messages = model.calls[0]
    assert [m.role for m in messages] == ["system", "user"]
    assert "технолог-программист" in messages[0].content
    prompt = messages[-1].content
    assert "РЕЖИМЫ РЕЗАНИЯ ДЛЯ МАТЕРИАЛА «Алюминиевый сплав»" in prompt
    # Только типы операций из запроса
    assert "Подрезка торца: скорость резания 250–400" in prompt
    assert "Сверление: скорость резания 60–90" in prompt
    assert "Нарезание резьбы:" not in prompt


async def test_prompt_has_no_concrete_numeric_examples():
    model = ScriptedChatModel([GOOD])
    await Pipeline(model, make_search({}), max_fix_attempts=3).run(MACHINE, "steel", [OPS[1]])
    prompt = model.calls[0][-1].content
    # Конкретные числа из примеров модель переносит дословно
    for stale in ("Z-42", "X84", "X62", "Z-25 Q5000", "CYCLE83(5,0,1,-25"):
        assert stale not in prompt


BASE_PROGRAM = "T0101\nG97 S800 M3\nG0 X50 Z2\nG1 Z-10 F0.2\nM30"
EDITED = "Подачу изменил.\n\n```nc\nT0101\nG97 S800 M3\nG0 X50 Z2\nG1 Z-10 F0.3\nM30\n```"


async def test_edit_sends_current_program_and_instruction():
    model = ScriptedChatModel([EDITED])
    stages = []

    async def on_stage(stage, attempt):
        stages.append(stage)

    result = await Pipeline(model, make_search({}), max_fix_attempts=3).edit(
        MACHINE, "steel", BASE_PROGRAM, "Подачу сделать 0.3 мм/об", [], on_stage
    )
    prompt = model.calls[0][-1].content
    assert "ИНСТРУКЦИЯ ТЕХНОЛОГА" in prompt and "Подачу сделать 0.3 мм/об" in prompt
    assert "G1 Z-10 F0.2" in prompt  # текущая программа
    assert "НОВЫЕ ОПЕРАЦИИ" not in prompt
    assert stages == ["editing", "validating"]
    assert result.sources == [] and "F0.3" in result.program_text
    # Число из инструкции есть в программе: предупреждений нет
    assert result.report.issues == []


async def test_edit_warns_when_instruction_value_is_missing():
    model = ScriptedChatModel([EDITED])
    result = await Pipeline(model, make_search({}), max_fix_attempts=3).edit(
        MACHINE, "steel", BASE_PROGRAM, "Подачу сделать 0.5 мм/об", []
    )
    assert [i.code for i in result.report.issues] == ["REQUEST_VALUE_MISSING"]
    assert "Инструкция" in result.report.issues[0].message


async def test_edit_with_new_operations_searches_and_continues_numbering():
    search = make_search({"drilling": hit("fanuc_turn", "G83 X0 Z-42 F0.1")})
    model = ScriptedChatModel([GOOD])
    stages = []

    async def on_stage(stage, attempt):
        stages.append(stage)

    result = await Pipeline(model, search, max_fix_attempts=3).edit(
        MACHINE, "steel", BASE_PROGRAM, "", [OPS[1]], on_stage, first_index=3
    )
    assert stages == ["searching", "editing", "validating"]
    assert [s.index for s in result.sources] == [3] and result.sources[0].kind == "kb"
    prompt = model.calls[0][-1].content
    assert "НОВЫЕ ОПЕРАЦИИ" in prompt and "Операция 3:" in prompt and "G83 X0 Z-42 F0.1" in prompt


async def test_edit_runs_fix_loop():
    model = ScriptedChatModel([NO_END, GOOD])
    result = await Pipeline(model, make_search({}), max_fix_attempts=3).edit(
        MACHINE, "steel", BASE_PROGRAM, "Убрать конец программы", []
    )
    assert result.attempts == 1 and result.report.errors == []


async def test_demo_model_in_edit_mode_returns_current_program():
    result = await Pipeline(DemoChatModel(), make_search({}), max_fix_attempts=3).edit(
        MACHINE, "steel", BASE_PROGRAM, "Поменять подачу", []
    )
    assert result.program_text == BASE_PROGRAM


async def test_demo_model_in_edit_mode_inserts_new_operations_before_last_line():
    search = make_search({"drilling": hit("fanuc_turn", "T1111\nG83 X0 Z-42 F0.1")})
    result = await Pipeline(DemoChatModel(), search, max_fix_attempts=3).edit(
        MACHINE, "steel", BASE_PROGRAM, "", [OPS[1]]
    )
    lines = result.program_text.splitlines()
    assert lines[-1] == "M30" and "G83 X0 Z-42 F0.1" in lines
    assert lines.index("G83 X0 Z-42 F0.1") < len(lines) - 1
