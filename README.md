# RAG-помощник технолога (прототип)

Прототип ядра MVP: генерация управляющих программ для станков с ЧПУ на основе похожих операций из базы знаний. Документация:

- [docs/HOW_IT_WORKS.md](docs/HOW_IT_WORKS.md) — как работает система простыми словами: данные и этапы от запроса до программы;
- [docs/GUIDE.md](docs/GUIDE.md) — как пользоваться и как проект устроен, обзорно;
- [docs/TESTING.md](docs/TESTING.md) — что вводить в поля, чтобы проверить работоспособность;
- [docs/INTERNALS.md](docs/INTERNALS.md) — подробное описание важных блоков;
- [docs/NOTES.md](docs/NOTES.md) — итоги прототипа;
- [docs/EVAL.md](docs/EVAL.md) — результаты оценки поиска и генерации;
- [docs/PLAN.md](docs/PLAN.md) — исходный план разработки;
- [docs/IMPROVEMENT_PLAN.md](docs/IMPROVEMENT_PLAN.md) — план доработки генерации и RAG (выполнен).

## Запуск

Нужны Docker с Compose и Node.js. Для минимального запуска (на заглушках, без сети и без загрузки моделей) достаточно команд ниже.

```bash
cp .env.example .env
```

Если `.env` уже есть от прежней версии, он продолжит работать: новые переменные (`EMBEDDINGS_URL`, `RERANKER_*`, `SEARCH_CANDIDATES` и др.) необязательны и имеют значения по умолчанию. Их описание — в `.env.example`.

```bash
docker compose up -d --build
```

Эта команда поднимает базу и бэкенд. Контейнеры моделей поиска (`embeddings`, `reranker`) в неё **не входят** и запускаются отдельно, см. раздел «Эмбеддинги bge-m3».

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

## Загрузка своих программ

Готовая УП (текстовый файл) режется на операции автоматически. Результат — черновик, который проверяет человек:

```bash
docker compose exec backend python -m seed.ingest seed/incoming/prog.nc --machine "Токарный станок с ЧПУ Fanuc 0i-TD" --material steel
```

Скрипт определяет границы операций (по вызову инструмента), их тип (по циклам и перемещениям), описывает каждую операцию и записывает `backend/seed/programs/drafts/prog.yaml` в формате `seed/programs/*.yaml`. Описания пишет модель (`LLM_PROVIDER=gigachat`) или шаблон по признакам кода (`--describe template`, по умолчанию при `LLM_PROVIDER=mock`). Если тип операции не определился, он указывается флагом `--types facing,turning,...`.

Проверьте в черновике типы, границы и описания, перенесите файл в `backend/seed/programs/` и загрузите в базу: `python -m seed.seed`.

## Оценка качества

```bash
docker compose exec backend python -m eval.run_search
docker compose exec backend python -m eval.run_generation --repeats 3
```

Результаты и их разбор — в [docs/EVAL.md](docs/EVAL.md).

## Эмбеддинги bge-m3

По умолчанию поиск работает на заглушке (хеши слов). Смысловой поиск — модель `BAAI/bge-m3` в контейнере `embeddings` (Text Embeddings Inference, CPU, ~3 ГБ памяти; модель ~2,3 ГБ скачивается при первом запуске, загрузка занимает несколько минут):

```bash
docker compose up -d embeddings
```

Первый запуск скачивает модель (несколько минут), готовность — когда `http://localhost:8081/health` отвечает 200. Контейнер входит в профиль `models`, поэтому обычный `docker compose up -d` его не запускает.

В `.env`: `EMBEDDING_PROVIDER=tei`. Затем примените настройки (`docker compose up -d backend`: простой `restart` переменные из `.env` не перечитывает), перезагрузите базу знаний и подберите порог поиска:

```bash
docker compose exec backend python -m app.llm.check_embeddings
docker compose exec backend python -m seed.seed --reset
docker compose exec backend python -m eval.run_search
```

Рекомендуемое значение порога из вывода `eval.run_search` впишите в `SEARCH_MAX_DISTANCE` (на учебных данных подошло 0.35).

Реранкер (`bge-reranker-v2-m3`, ещё ~2,5 ГБ памяти) включается так же: `docker compose up -d reranker` (готовность — `http://localhost:8082/health`), в `.env` `RERANKER_PROVIDER=tei`, затем `docker compose up -d backend` (простой `restart` переменные из `.env` не перечитывает). Он снижает число ложно найденных образцов; сравнение схем — в [docs/EVAL.md](docs/EVAL.md). Если оба контейнера не запускаются из-за памяти, увеличьте лимит памяти Docker Desktop.

## Логи

Что происходит с запросом после отправки, видно в логах бэкенда:

```bash
docker compose logs -f backend
```

Каждая строка внутри генерации помечена номером запуска (`[запуск 13]`): видно создание запуска, поиск источника по каждой операции (сколько найдено, с каким расстоянием или оценкой реранкера), вызовы модели с длительностью и числом токенов, результаты проверки с номерами строк, попытки исправления и итог. Подробности и пример — в [docs/GUIDE.md](docs/GUIDE.md), раздел «Логи». Уровень задаётся `LOG_LEVEL` в `.env` (`DEBUG` добавляет полные тексты запросов к модели и ответов).

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
