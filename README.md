# RAG-помощник технолога (прототип)

Прототип ядра MVP: генерация управляющих программ для станков с ЧПУ на основе похожих операций из базы знаний. Документация:

- [docs/GUIDE.md](docs/GUIDE.md) — как пользоваться и как проект устроен, обзорно;
- [docs/TESTING.md](docs/TESTING.md) — что вводить в поля, чтобы проверить работоспособность;
- [docs/INTERNALS.md](docs/INTERNALS.md) — подробное описание важных блоков;
- [docs/NOTES.md](docs/NOTES.md) — итоги прототипа;
- [docs/PLAN.md](docs/PLAN.md) — план разработки;
- [docs/IMPROVEMENT_PLAN.md](docs/IMPROVEMENT_PLAN.md) — план доработки генерации и RAG.

## Запуск

Нужны Docker с Compose и Node.js.

```bash
cp .env.example .env
```

```bash
docker compose up -d --build
```

```bash
docker compose exec backend alembic upgrade head
```

Наполнение базы знаний (станки, примеры УП):

```bash
docker compose exec backend python -m seed.seed
```

API: http://localhost:8000/api/health, описание — http://localhost:8000/docs.

Клиент:

```bash
cd frontend && npm install && npm run dev
```

Откроется на http://localhost:5173.

## Тесты

```bash
docker compose exec backend pytest
```

## GigaChat

По умолчанию `LLM_PROVIDER=mock` — система работает на заглушке без обращения к сети. Для живой модели впишите в `.env` ключ `GIGACHAT_AUTH_KEY` и поставьте `LLM_PROVIDER=gigachat`. Корневые сертификаты Минцифры должны лежать в `backend/certs/russian_trusted_root_ca.pem`.

Проверка связи:

```bash
docker compose exec backend python -m app.llm.check
```

Эмбеддинги выбираются отдельно переменной `EMBEDDING_PROVIDER`. Значение `mock` даёт локальные векторы на хешах слов (поиск по совпадению слов); `gigachat` требует оплаченного пакета эмбеддингов. После смены источника эмбеддингов базу знаний нужно загрузить заново:

```bash
docker compose exec backend python -m seed.seed --reset
```

Бэкенд в контейнере запущен с автоперезагрузкой: правка файлов в `backend/` во время генерации перезапускает сервер и обрывает запуск.
