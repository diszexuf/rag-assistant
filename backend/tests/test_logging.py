import logging

from app.kb.search import OperationHit
from app.llm.mock import ScriptedChatModel
from app.logging_setup import FORMAT, RunIdFilter, run_id_var, setup_logging
from app.orchestrator.pipeline import MachineContext, OperationRequest, Pipeline

MACHINE = MachineContext(
    name="Тестовый токарный",
    kind="lathe",
    dialect="fanuc_turn",
    max_spindle_rpm=3000,
    tools=[{"position": 1, "name": "Резец", "type": "проходной"}],
)
NO_END = "Готово.\n\n```nc\nT0101\nG97 S800 M3\nG1 Z-10 F0.2\n```"
GOOD = "Готово.\n\n```nc\nT0101\nG97 S800 M3\nG1 Z-10 F0.2\nM30\n```"


async def no_hits(query, filters):
    return []


async def one_hit(query, filters):
    return [OperationHit(7, 3, "УП из базы", "fanuc_turn", "turning", "d", "G1 Z-10", 0.123)]


def messages(caplog) -> str:
    return "\n".join(record.getMessage() for record in caplog.records if record.name == "pipeline")


async def test_pipeline_logs_search_model_validation_and_fix(caplog):
    caplog.set_level(logging.INFO)
    model = ScriptedChatModel([NO_END, GOOD])
    pipeline = Pipeline(model, one_hit, max_fix_attempts=3)
    await pipeline.run(MACHINE, "steel", [OperationRequest("turning", "точение вала")])

    text = messages(caplog)
    assert "Новая программа: станок «Тестовый токарный»" in text
    assert "операция 1 [turning]: точение вала" in text
    assert "Поиск операция 1 (Наружное точение), попытка 1" in text
    assert "источник из базы знаний: «УП из базы»" in text and "расстояние 0.123" in text
    assert "Запрос к модели (генерация)" in text and "Ответ модели (генерация)" in text
    assert "ошибка NO_PROGRAM_END" in text
    assert "Исправление, попытка 1 из 3" in text and "Исправление принято: ошибок 1 -> 0" in text
    assert "Итог: программа без ошибок" in text


async def test_pipeline_logs_missing_source_and_early_stop(caplog):
    caplog.set_level(logging.INFO)
    model = ScriptedChatModel([NO_END, NO_END])
    await Pipeline(model, no_hits, max_fix_attempts=3).run(
        MACHINE, "steel", [OperationRequest("drilling", "сверление")]
    )
    text = messages(caplog)
    assert "источника нет: В базе знаний нет похожих операций" in text
    assert "Исправление не помогло (ошибок 1 -> 1)" in text
    assert "Итог: программа отдаётся с ошибками (1)" in text


async def test_prompts_and_responses_are_logged_only_at_debug(caplog):
    caplog.set_level(logging.INFO)
    await Pipeline(ScriptedChatModel([GOOD]), no_hits, max_fix_attempts=1).run(
        MACHINE, "steel", [OperationRequest("facing", "подрезка")]
    )
    assert "Запрос к модели (генерация), user:" not in messages(caplog)

    caplog.clear()
    caplog.set_level(logging.DEBUG)
    await Pipeline(ScriptedChatModel([GOOD]), no_hits, max_fix_attempts=1).run(
        MACHINE, "steel", [OperationRequest("facing", "подрезка")]
    )
    text = messages(caplog)
    assert "Запрос к модели (генерация), user:" in text and "Ответ модели (генерация):" in text


def test_run_id_is_added_to_records():
    record = logging.LogRecord("x", logging.INFO, __file__, 1, "msg", None, None)
    token = run_id_var.set("42")
    try:
        RunIdFilter().filter(record)
    finally:
        run_id_var.reset(token)
    assert record.run_id == "42"
    assert "[запуск 42]" in logging.Formatter(FORMAT).format(record)


def test_setup_logging_sets_level_and_quiets_libraries():
    setup_logging("debug")
    assert logging.getLogger().level == logging.DEBUG
    assert logging.getLogger("httpx").level == logging.WARNING
    setup_logging("INFO")
    assert logging.getLogger().level == logging.INFO
