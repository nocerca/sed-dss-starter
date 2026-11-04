# Настройка и обслуживание

Все команды выполняются из корня проекта в PowerShell. Для обычного запуска достаточно Docker Desktop и Python для вспомогательных скриптов. Модель, GPU и Node.js на компьютере не нужны: OpenClaw работает в контейнере.

## Локальный запуск без модели

```powershell
python scripts/setup_local.py
docker compose config --quiet
docker compose up -d --build
python scripts/smoke_test.py
```

`setup_local.py` создаёт `.env`, если его нет, и заполняет недостающие внутренние секреты без вывода значений. Существующие настройки сохраняются. `.env`, локальные документы, виртуальное окружение и резервные копии исключены из образа и Git. Сейчас папка проекта не содержит `.git`; правила исключения пригодятся при создании репозитория.

Откройте [Swagger](http://127.0.0.1:8000/docs), нажмите **Authorize**, вставьте значение `APP_API_KEY` из локального `.env`. Для `/api/v1/analyze` выберите DOCX; оставьте `use_llm=false`. HTTP API слушает только `127.0.0.1:8000`.

```powershell
docker compose ps
docker compose stop
docker compose start
```

Остановка через `docker compose down` сохраняет PostgreSQL. Не добавляйте `-v`: это удаляет том с данными. Пароль БД из шаблона рассчитан на локальную разработку; наружу БД не опубликована.

## OpenClaw и выбор backend

OpenClaw уже настроен проектом: ручной onboarding, установка расширений и отдельный пользовательский агент не требуются. Файлы `deploy/openclaw-config.mjs` и `deploy/openclaw-entrypoint.mjs` генерируют и проверяют конфигурацию закреплённой версии контейнера. Агент `sed-dss` получает только переданный текст; инструменты, команды и браузер отключены.

Бесплатный кандидат и получение ключа подробно описаны в [SETUP_ZAI.md](SETUP_ZAI.md). Общие настройки для любого backend:

```dotenv
LLM_PROVIDER=openclaw
ALLOW_EXTERNAL_LLM=true
```

Для Z.AI задайте также `LLM_TIMEOUT_SECONDS=150`: шлюз допускает до 120 секунд обработки, клиент должен ждать дольше. Это предел ожидания, а не обещание времени ответа. Успешные синтетические проверки после подключения заняли около 11 и 93 секунд.

Дополнительно заполните в `.env` одну группу:

| Backend | Настройки |
|---|---|
| Z.AI | `MODEL_BACKEND=zai`, `ZAI_API_KEY`, `ZAI_MODEL=glm-4.7-flash` |
| Яндекс | `MODEL_BACKEND=yandex`, `YANDEX_API_KEY`, `YANDEX_FOLDER_ID`, `YANDEX_MODEL=aliceai-llm-flash` |
| GigaChat | `MODEL_BACKEND=gigachat`, `GIGACHAT_AUTH_KEY`, `GIGACHAT_SCOPE=GIGACHAT_API_PERS`, `GIGACHAT_MODEL=GigaChat-2`, внутренний `MODEL_BRIDGE_API_KEY` |

Названия моделей — текущие настройки проекта, доступ аккаунта проверяется отдельно. Backend Яндекса использует URI `gpt://<folder>/<model>/latest`. Официальные инструкции: [ключ Яндекса](https://yandex.cloud/ru/docs/ai-studio/quickstart/yandexgpt), [авторизация GigaChat](https://developers.sber.ru/docs/ru/gigachat/api/reference/rest/post-token). Бесплатный доступ Яндекса/Сбера здесь не обещается. OAuth-ключ GigaChat и внутренний токен bridge — разные секреты; `setup_local.py` генерирует второй независимо от выбранного backend.

После изменения `.env`:

```powershell
docker compose config --quiet
docker compose run --rm -e OPENCLAW_VALIDATE_ONLY=1 openclaw
docker compose up -d --build --force-recreate api openclaw
python scripts/smoke_test.py
python scripts/smoke_test.py --llm
```

Для GigaChat используйте вместо команды запуска:

```powershell
docker compose --profile gigachat up -d --build --force-recreate api openclaw model-bridge
```

`--llm` означает явное согласие передать встроенное синтетическое письмо. Без этого флага smoke работает локально. Двойное согласие требуется и в Swagger: `use_llm=true`, `allow_external_transfer=true` плюс глобальное разрешение. `partial` означает, что остались результаты правил, а модельная часть не завершилась. Проверяйте статус отчёта, а не только HTTP 200.

Ключ модели не передаётся основному API. Основной API использует внутренний `OPENCLAW_GATEWAY_TOKEN`; Compose подставляет его как `OPENCLAW_API_KEY`. При запуске API вне Compose задавайте достижимый адрес шлюза и тот же токен отдельно. По умолчанию шлюз не публикует порт на хост; готовый и проверенный маршрут — через Compose.

Отключение внешних запросов: `LLM_PROVIDER=none`, `ALLOW_EXTERNAL_LLM=false`, затем пересоздайте API. Повторы запросов 429 выключены: `LLM_MAX_RETRIES=0`. Можно разрешить до двух повторов с коротким числовым `Retry-After`; другие ошибки автоматически не повторяются.

## Данные, миграции и хранение

При старте API Alembic обновляет схему до актуальной версии. Совместимая прежняя схема принимается без удаления записей; несовместимая вызывает остановку запуска. Небезопасный downgrade отключён. Перед изменениями схемы делайте резервную копию; созданные локально копии лежат в игнорируемой папке `backups/`.

Исходные DOCX анализа не сохраняются, но отчёт содержит имя файла, цитаты и переданные метаданные. Загруженная база знаний сохраняется в PostgreSQL до явного удаления. История OpenClaw и кэш находятся в `tmpfs`: пересоздание контейнера убирает их; немедленное удаление после отдельного запроса и нулевое хранение внешним провайдером не подтверждены. Для первых модельных проверок используйте синтетические данные.

Просмотр числа отчётов старше 30 дней без изменений:

```powershell
docker compose exec api python -m app.retention --older-than-days 30
```

Для намеренного удаления добавьте `--execute`. Это удаляет только старые отчёты, оставляет маркеры идемпотентности и не затрагивает базу знаний. В текущей рабочей БД удаление не выполнялось. Отдельный отчёт или документ базы знаний можно удалить через REST.

## Локальная орфография и грамматика

В `.env` задайте `LANGUAGE_TOOL_ENABLED=true`. Затем:

```powershell
docker compose --profile language up -d --build
python scripts/smoke_test.py --language
```

В Swagger/Postman передавайте `use_language_checks=true`; для работы без внешней модели оставляйте `use_llm=false`, `allow_external_transfer=false`. Загрузка Word сама по себе этот модуль не включает. Для совместной проверки можно включить оба модуля с требуемым внешним согласием. Запрошенный, но недоступный локальный модуль даёт `partial`, сохраняя правила.

Используется LanguageTool 6.6 в отдельном контейнере с read-only файловой системой, временным `/tmp`, без порта на хост и без интернета. Облачные AI-правила, n-gram датасеты и публичный API не подключаются. Дистрибутив копируется из указанного в репозитории LanguageTool community-образа `erikvl87/languagetool:6.6`, закреплённого SHA256, в наш Java 17 runtime. Лицензия дистрибутива сохраняется в образе. Серверный вывод подавлен, кэш запросов отключён. `LANGUAGE_TOOL_TIMEOUT_SECONDS=20` — предел ожидания локального вызова.

Проверка ограничена началом текста до 20 000 единиц UTF-16 и может ошибаться на именах/терминах/коде. Тест на искусственных опечатках подтверждает подключение, не измеряет экспертное качество. Обновление движка требует проверки регрессий. [Официальная инструкция локального сервера](https://dev.languagetool.org/http-server).

Для остановки только этого сервиса: `docker compose --profile language stop languagetool`. Чтобы больше не запрашивать модуль, используйте `use_language_checks=false`; чтобы выключить адаптер глобально — `LANGUAGE_TOOL_ENABLED=false` и пересоздание API.

## Проверки для разработчика

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest -q
node --test tests/openclaw-config.test.mjs
.\.venv\Scripts\python.exe scripts/export_openapi.py --check
.\.venv\Scripts\python.exe scripts/check_gateway.py
```

Node.js нужен только для локального теста конфигурации. Последняя команда создаёт отдельный проект Docker с временной БД и внутренней сетью без интернета, запускает настоящий OpenClaw против тестового HTTP-сервера модели и затем убирает только собственные контейнеры. Рабочая БД и ключи моделей не используются. Это проверка интеграции, а не качества внешней модели.
