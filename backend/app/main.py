import logging
from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.api import router as chat_router
from app.config import get_settings
from app.db import SessionLocal, get_session
from app.llm.factory import get_chat_model, get_embedding_model
from app.orchestrator.runs import RunManager

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    app.state.runs = RunManager(
        SessionLocal,
        get_chat_model,
        get_embedding_model,
        run_timeout_s=settings.run_timeout_s,
        max_fix_attempts=settings.max_fix_attempts,
        search_top_k=settings.search_top_k,
        search_max_distance=settings.search_max_distance,
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
    }


app.include_router(api)
app.include_router(chat_router)
