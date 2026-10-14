import logging
from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.api import router as chat_router
from app.config import get_settings
from app.db import SessionLocal, get_session
from app.llm.factory import get_chat_model, get_embedding_model, get_reranker
from app.logging_setup import setup_logging
from app.orchestrator.runs import RunManager

setup_logging(get_settings().log_level)
logger = logging.getLogger("app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    app.state.runs = RunManager(
        SessionLocal,
        get_chat_model,
        get_embedding_model,
        get_reranker,
        run_timeout_s=settings.run_timeout_s,
        max_fix_attempts=settings.max_fix_attempts,
        search_top_k=settings.search_top_k,
        search_max_distance=settings.search_max_distance,
        search_candidates=settings.search_candidates,
        rerank_min_score=settings.rerank_min_score,
    )
    logger.info(
        "Запуск сервера: LLM=%s (модель %s), эмбеддинги=%s, реранкер=%s; поиск: top_k=%s, "
        "порог расстояния=%s, кандидатов=%s, мин. оценка реранкера=%s; "
        "исправлений до %s, таймаут запуска %s с, уровень логов %s",
        settings.llm_provider,
        settings.gigachat_model if settings.llm_provider == "gigachat" else "-",
        settings.embedding_provider,
        settings.reranker_provider,
        settings.search_top_k,
        settings.search_max_distance,
        settings.search_candidates,
        settings.rerank_min_score,
        settings.max_fix_attempts,
        settings.run_timeout_s,
        settings.log_level,
    )
    await app.state.runs.fail_stale_runs()
    yield


app = FastAPI(title="RAG-помощник технолога", lifespan=lifespan)

api = APIRouter(prefix="/api")


@api.get("/health")
async def health(session: AsyncSession = Depends(get_session)) -> dict:
    await session.execute(text("select 1"))
    settings = get_settings()
    return {
        "status": "ok",
        "llm_provider": settings.llm_provider,
        "embedding_provider": settings.embedding_provider,
        "reranker_provider": settings.reranker_provider,
    }


app.include_router(api)
app.include_router(chat_router)
