import asyncio
import logging
import ssl
import time
import uuid
from pathlib import Path

import httpx

from app.llm.base import ChatMessage, LLMError

logger = logging.getLogger("llm.gigachat")

AUTH_URL = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
API_URL = "https://gigachat.devices.sberbank.ru/api/v1"
# Обновляем токен заранее, не дожидаясь истечения
TOKEN_RESERVE_S = 60
RETRIES = 2


class GigaChatClient:
    """Чат и эмбеддинги GigaChat. Реализует ChatModel и EmbeddingModel."""

    def __init__(
        self,
        auth_key: str,
        scope: str,
        model: str,
        embedding_model: str,
        timeout_s: float,
        verify_ssl: bool = True,
        ca_bundle: str = "",
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        if not auth_key:
            raise LLMError("Не задан GIGACHAT_AUTH_KEY")
        self._auth_key = auth_key
        self._scope = scope
        self._model = model
        self._embedding_model = embedding_model
        self._http = httpx.AsyncClient(
            timeout=timeout_s,
            verify=_ssl_verify(verify_ssl, ca_bundle),
            transport=transport,
        )
        self._token: str | None = None
        self._token_expires_at = 0.0
        self._token_lock = asyncio.Lock()

    async def complete(self, messages: list[ChatMessage], **params) -> str:
        payload = {
            "model": self._model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": params.get("temperature", 0.1),
        }
        data = await self._post("/chat/completions", payload)
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as exc:
            raise LLMError(f"Неожиданный ответ GigaChat: {data}") from exc

    async def embed(self, texts: list[str]) -> list[list[float]]:
        data = await self._post("/embeddings", {"model": self._embedding_model, "input": texts})
        try:
            items = sorted(data["data"], key=lambda item: item["index"])
            return [item["embedding"] for item in items]
        except (KeyError, TypeError) as exc:
            raise LLMError(f"Неожиданный ответ GigaChat: {data}") from exc

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _post(self, path: str, payload: dict) -> dict:
        last_error: Exception | None = None
        for attempt in range(RETRIES + 1):
            started = time.monotonic()
            try:
                token = await self._get_token()
                response = await self._http.post(
                    f"{API_URL}{path}",
                    json=payload,
                    headers={"Authorization": f"Bearer {token}"},
                )
                if response.status_code == 401:
                    self._token = None
                    raise LLMError("GigaChat отклонил токен")
                if response.status_code == 429 or response.status_code >= 500:
                    raise LLMError(f"GigaChat вернул {response.status_code}: {response.text[:300]}")
                if response.status_code >= 400:
                    # Ошибка запроса: повтор не поможет
                    self._log(path, payload, started, error=True)
                    raise _Fatal(f"GigaChat вернул {response.status_code}: {response.text[:300]}")
                data = response.json()
                self._log(path, payload, started, usage=data.get("usage"))
                return data
            except _Fatal as exc:
                raise LLMError(str(exc)) from exc
            except (httpx.HTTPError, LLMError) as exc:
                last_error = exc
                self._log(path, payload, started, error=True)
                if attempt < RETRIES:
                    await asyncio.sleep(1.5 * (attempt + 1))
        raise LLMError(f"GigaChat недоступен: {last_error!r}") from last_error

    async def _get_token(self) -> str:
        async with self._token_lock:
            if self._token and time.time() < self._token_expires_at - TOKEN_RESERVE_S:
                return self._token
            response = await self._http.post(
                AUTH_URL,
                data={"scope": self._scope},
                headers={
                    "Authorization": f"Basic {self._auth_key}",
                    "RqUID": str(uuid.uuid4()),
                    "Accept": "application/json",
                },
            )
            if response.status_code != 200:
                raise _Fatal(
                    f"Не удалось получить токен GigaChat: {response.status_code} {response.text[:300]}"
                )
            data = response.json()
            self._token = data["access_token"]
            # expires_at приходит в миллисекундах
            self._token_expires_at = data["expires_at"] / 1000
            return self._token

    def _log(self, path: str, payload: dict, started: float, usage=None, error=False) -> None:
        logger.info(
            "gigachat call path=%s model=%s duration=%.2fs tokens=%s error=%s",
            path,
            payload.get("model"),
            time.monotonic() - started,
            (usage or {}).get("total_tokens"),
            error,
        )


class _Fatal(Exception):
    """Ошибка, при которой повторять запрос бессмысленно."""


def _ssl_verify(verify_ssl: bool, ca_bundle: str) -> ssl.SSLContext | bool:
    if not verify_ssl:
        return False
    if ca_bundle and Path(ca_bundle).is_file():
        # Сертификаты Минцифры добавляются к системным, а не заменяют их
        context = ssl.create_default_context()
        context.load_verify_locations(cafile=ca_bundle)
        return context
    return True
