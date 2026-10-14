"""Настройка логов: у каждой строки внутри запуска генерации есть его номер."""

import logging
from contextvars import ContextVar

# Номер запуска генерации; задаётся в начале фоновой задачи и виден во всех записях внутри неё
run_id_var: ContextVar[str] = ContextVar("run_id", default="-")

_handler: logging.Handler | None = None

FORMAT = "%(asctime)s %(levelname)-7s [запуск %(run_id)s] %(name)s: %(message)s"


class RunIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.run_id = run_id_var.get()
        return True


def setup_logging(level: str = "INFO") -> None:
    """Один обработчик на корневом логгере; шумные библиотеки приглушены.

    INFO — ход работы: запрос, поиск, вызовы модели, проверка, итог.
    DEBUG — дополнительно полные тексты запросов к модели и ответов.
    """
    global _handler
    root = logging.getLogger()
    # Снимаем только свой обработчик: чужие (например, перехват логов в тестах) не трогаем
    if _handler is not None:
        root.removeHandler(_handler)
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter(FORMAT))
    _handler.addFilter(RunIdFilter())
    root.addHandler(_handler)
    root.setLevel(level.upper())

    # httpx пишет строку на каждый HTTP-вызов; важное мы логируем сами с длительностью
    for noisy in ("httpx", "httpcore", "sqlalchemy.engine", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
