import hmac
from collections import Counter
from contextlib import asynccontextmanager
from pathlib import Path
from time import perf_counter
from typing import Literal
from uuid import UUID

from fastapi import (
    Body,
    Depends,
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
)
from fastapi import Path as ApiPath
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader
from sqlalchemy import select, text, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app import api_docs
from app.analysis_cache import AnalysisCache
from app.assistant import FAILED_MESSAGE, assist_finding
from app.assistant_schemas import AssistantRequest, AssistantResponse
from app.config import get_settings
from app.db import SessionLocal, engine, get_db
from app.diagnostics import local_warnings
from app.document_context import document_profile
from app.document_parser import DocumentParseError, parse_docx, parse_knowledge
from app.frontend import FRONTEND_CSP, STATIC_DIRECTORY, FrontendFiles
from app.frontend import router as frontend_router
from app.idempotency import fingerprints, persist_run, replay
from app.language import LanguageCheckError, check_language
from app.letter_checks import IMPLEMENTED, PROFILE_ID, evaluate_policy
from app.migrations import upgrade_database
from app.models import (
    AnalysisRun,
    AssistantOperation,
    IdempotencyRecord,
    KnowledgeDocument,
    RuleDefinition,
    RuleVersion,
)
from app.providers import ProviderError, analyze_with_llm
from app.retrieval import retrieve
from app.rule_registry import RuleSet, load_registry, register_rules
from app.rules import applicable_rules, inspect_document
from app.schemas import AnalysisResponse, DocumentType, Finding, KnowledgeItem, SourceKind


@asynccontextmanager
async def lifespan(app: FastAPI):
    upgrade_database(engine)
    with SessionLocal() as db:
        register_rules(db, load_registry())
    settings = get_settings()
    app.state.analysis_cache = AnalysisCache(settings.analysis_cache_ttl_seconds,
                                             settings.analysis_cache_max_entries)
    try:
        yield
    finally:
        app.state.analysis_cache.clear()


app = FastAPI(title="SED DSS — анализ исходящих писем", version="0.8.0", lifespan=lifespan,
              description=api_docs.API_DESCRIPTION,
              openapi_tags=[
                  {"name": "Анализ", "description": "Проверка DOCX и объяснение результата. Решение принимает человек."},
                  {"name": "Отчёты", "description": "Локально сохранённые результаты; чтение не вызывает модель повторно."},
                  {"name": "Помощник", "description": "Объяснение одного замечания и предложения автору. Оригинал не изменяется, ответы модели проверяет человек."},
                  {"name": "Правила", "description": "Версии эвристик, основания и область применимости."},
                  {"name": "База знаний", "description": "Материалы для поиска контекста. Загружайте только разрешённые данные."},
                  {"name": "Сервис", "description": "Состояние API/БД и настройки возможностей."},
              ],
              swagger_ui_parameters={"defaultModelRendering": "model", "displayRequestDuration": True})

app.include_router(frontend_router)
app.mount("/assets", FrontendFiles(directory=STATIC_DIRECTORY), name="assets")


@app.middleware("http")
async def trace_request(request: Request, call_next):
    from uuid import uuid4
    request.state.request_id = str(uuid4())
    started = perf_counter()
    response = await call_next(request)
    if request.url.path.startswith('/api/v1/'):
        response.headers['Cache-Control'] = 'no-store'
    if request.url.path == '/' or request.url.path.startswith('/assets/'):
        response.headers['Cache-Control'] = 'no-store'
        response.headers['Content-Security-Policy'] = FRONTEND_CSP
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Permissions-Policy'] = 'camera=(), microphone=(), geolocation=()'
    response.headers["X-Request-ID"] = request.state.request_id
    response.headers["Server-Timing"] = f"app;dur={(perf_counter() - started) * 1000:.2f}"
    return response


api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False,
    description="Ключ нашего приложения: значение APP_API_KEY из локального .env. Вставьте без кавычек, имени переменной и префикса Bearer. ZAI_API_KEY, YANDEX_API_KEY и токен OpenClaw здесь не подходят.")


def require_api_key(x_api_key: str | None = Depends(api_key_header)):
    expected = get_settings().app_api_key
    if not expected or not x_api_key or not hmac.compare_digest(x_api_key.encode(), expected.encode()):
        raise HTTPException(status_code=401, detail="Неверный X-API-Key")


@app.get("/health/live", tags=["Сервис"], summary="Проверить живость API",
         description="Открытый endpoint без ключа. status=ok означает, что процесс отвечает; БД, шлюз и модель этим запросом не проверяются.")
def health_live():
    return {"status": "ok"}


@app.get("/health/ready", tags=["Сервис"], summary="Проверить доступность БД",
         description="Открытый endpoint: status=ready при успешном запросе к БД. Не проверяет модель или OpenClaw: локальные правила должны работать независимо от них.",
         responses={503: {"description": "БД недоступна; status=db_unavailable."}})
def health_ready(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
        return {"status": "ready"}
    except Exception:
        return JSONResponse(status_code=503, content={"status": "db_unavailable"})


@app.get("/api/v1/capabilities", dependencies=[Depends(require_api_key)], tags=["Сервис"],
         summary="Посмотреть возможности и текущие настройки", description=api_docs.CAPABILITIES_DESCRIPTION,
         responses=api_docs.AUTH_RESPONSE)
def capabilities():
    s = get_settings()
    return {"accepted_analysis_formats": [".docx"],
            "accepted_knowledge_formats": [".docx", ".txt"],
            "current_provider": s.llm_provider, "external_llm_enabled": s.allow_external_llm,
            "required_llm_gateway": "openclaw", "direct_model_calls": False,
            "max_document_bytes": s.max_document_bytes,
            "local_language_checks_available": s.language_tool_enabled,
            "policy_profiles": [PROFILE_ID], "implemented_policy_checks": list(IMPLEMENTED),
            "analysis_cache": {"enabled": s.analysis_cache_enabled, "scope": "process",
                               "ttl_seconds": s.analysis_cache_ttl_seconds,
                               "max_entries": s.analysis_cache_max_entries,
                               "stages": ["language_tool", "llm"], "stores": "report_references_only"},
            "finding_assistant": {"available": True, "local_actions": ['explain', 'fix'],
                                  "model_actions": ['explain', 'fix', 'rephrase', 'question'],
                                  "max_selected_text_characters": 2000, "max_question_characters": 1000,
                                  "model_idempotency_required": True, "conversation_history": False},
            "automatic_document_rewrite": False, "sed_integration": "REST API"}


async def read_limited(upload: UploadFile, size_limit: int) -> bytes:
    data = await upload.read(size_limit + 1)
    if len(data) > size_limit:
        raise HTTPException(status_code=413, detail="Размер файла превышает лимит")
    if not data:
        raise HTTPException(status_code=422, detail="Пустой файл")
    return data


def as_response(run: AnalysisRun) -> AnalysisResponse:
    return AnalysisResponse(id=run.id, filename=run.filename, mode=run.mode,
          provider=run.provider, status=run.status,
          findings=[Finding.model_validate(f) for f in run.findings],
          metrics=run.metrics, warnings=run.warnings, created_at=run.created_at,
          external_document_id=run.external_document_id, request_id=run.request_id,
          ruleset_version=run.ruleset_version, document_type=run.document_type,
          template_id=run.template_id)


def count_metrics(findings: list[Finding], paragraph_count: int) -> dict:
    categories = Counter(x.category for x in findings)
    severities = Counter(x.severity for x in findings)
    return {"total_findings": len(findings), "by_category": dict(categories),
            "by_severity": dict(severities), "paragraph_count": paragraph_count,
            "quality_score": None}


@app.post("/api/v1/analyze", response_model=AnalysisResponse,
          dependencies=[Depends(require_api_key)], tags=["Анализ"], summary="Проверить исходящее письмо DOCX",
          description=api_docs.ANALYZE_DESCRIPTION, responses=api_docs.ANALYZE_RESPONSES)
async def analyze(request: Request, response: Response, file: UploadFile = File(...,
                  description="Исходящее деловое письмо DOCX. По умолчанию до 5 МиБ; фактический лимит показывает /capabilities. Передаётся файл, не строка пути. Извлекаются текст и часть структуры; изображения не анализируются. Оригинал не изменяется."),
                  use_llm: bool = Form(False, description="Нужна ли модельная часть. false — локальная обработка; true — дополнительно модель через OpenClaw. LanguageTool включается отдельным флагом use_language_checks. Для LLM нужны также allow_external_transfer=true и глобальное разрешение сервера. По умолчанию false."),
                  use_language_checks: bool = Form(False, description="Добавить локальную проверку русского текста LanguageTool 6.6: орфография, часть грамматики и пунктуации. Нужны профиль Docker language и LANGUAGE_TOOL_ENABLED=true. Внешняя модель не используется. Начальная проверка ограничена 20 000 единицами UTF-16; усечение отражается в warnings. По умолчанию false."),
                  use_cache: bool = Form(True, description="Для запросов с моделью или LanguageTool разрешить повторное использование успешного отчёта по тем же байтам файла, метаданным, параметрам и конфигурации. По умолчанию true; TTL и включение — /capabilities. Вернётся прежний id/created_at/тайминги отчёта, Analysis-Cache=hit. Для нового вычисления задайте false и новый Idempotency-Key; false обходит чтение и запись кэша. Идемпотентный повтор со старым ключом всё равно возвращает прежний отчёт. Только локальные правила без языка/модели не кэшируются."),
                  allow_external_transfer: bool = Form(False, description="Явное согласие именно на этот запрос: передать извлечённый текст и найденные фрагменты базы знаний внешней модели через OpenClaw. Автоматического обезличивания нет. Без true модельный запрос отклоняется с 409. Само согласие при use_llm=false не вызывает модель."),
                  external_document_id: str | None = Form(None, min_length=1, max_length=128,
                  description="Необязательный идентификатор документа в вашей СЭД, 1–128 символов. Для ручного теста можно не заполнять. Сохраняется в отчёте локально, не включается в модельный запрос и не заменяет Idempotency-Key."),
                  document_type: DocumentType = Form(DocumentType.unspecified,
                  description="Тип письма для применимости правил: unspecified — не указан, request — запрос, response — ответ, notification — уведомление, cover_letter — сопроводительное. Это тип документа, не тип HTTP-запроса. Клиент указывает его самостоятельно; содержимое автоматически не классифицируется."),
                  template_id: str | None = Form(None, pattern=r"^[A-Za-z0-9_.-]{1,64}$",
                  description="Необязательный идентификатор корпоративного шаблона: 1–64 символа ASCII (буквы, цифры, _, ., -). Фильтрует правила по применимости; не загружает шаблон и не проверяет визуальное соответствие ему. Не заполняйте, если шаблон не определён."),
                  policy_profile: Literal["office_instruction_letters"] | None = Form(None,
                  description="Явно применить профиль предоставленной инструкции: office_instruction_letters. Без поля выполняются прежние эвристики. Профиль организации автоматически не угадывается. Включает реализованную часть требований; результаты и недостаточные данные — metrics.policy_checks. Не означает полного соответствия инструкции/ГОСТ."),
                  idempotency_key: str | None = Header(None, alias="Idempotency-Key", pattern=r"^[A-Za-z0-9_.:-]{8,128}$",
                  description="Необязательный идентификатор одной операции анализа; не API-ключ и не секрет модели. Например UUID. 8–128 символов ASCII: буквы, цифры, _, ., :, -. Для повтора тех же байтов файла, имени и параметров используйте тот же ключ: вернётся прежний отчёт. Изменённый запрос — 409, обработка ещё идёт — 409, отчёт удалён — 410. Для нового анализа нужен новый ключ. Если не нужен — уберите заголовок целиком; пустое значение даёт 422. {{llm_idempotency_key}} — переменная коллекции Postman, не буквальное значение для Swagger."),
                  db: Session = Depends(get_db)):
    s = get_settings()
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Analysis-Cache'] = 'bypass'
    started = perf_counter()
    timings = {stage: None for stage in ("parse_ms", "profile_ms", "rules_ms", "language_ms",
                                         "retrieval_ms", "llm_gateway_ms", "initial_save_ms")}
    filename = (file.filename or "unknown").replace("\\", "/").rsplit("/", 1)[-1][:255]
    if Path(filename).suffix.lower() != ".docx":
        raise HTTPException(415, "Проверка исходящей корреспонденции пока поддерживает только DOCX")
    if use_llm and (s.llm_provider == "none" or not s.allow_external_llm or not allow_external_transfer):
        raise HTTPException(status_code=409, detail=(
            "Для внешней LLM нужны настроенный провайдер, ALLOW_EXTERNAL_LLM=true и "
            "allow_external_transfer=true в каждом запросе"))
    data = await read_limited(file, s.max_document_bytes)
    key_hash = request_hash = None
    if idempotency_key:
        options = {
            "filename": filename, "use_llm": use_llm, "allow_external_transfer": allow_external_transfer,
            "external_document_id": external_document_id, "document_type": document_type.value,
            "template_id": template_id,
        }
        # Preserve fingerprints of old requests that did not ask for this new feature.
        if use_language_checks:
            options["use_language_checks"] = True
        if policy_profile:
            options["policy_profile"] = policy_profile
        if not use_cache:
            options['use_cache'] = False
        key_hash, request_hash = fingerprints(s.idempotency_secret, idempotency_key, data, options)
        if record := db.get(IdempotencyRecord, key_hash):
            response.headers["Idempotency-Replayed"] = "true"
            return as_response(replay(db, record, request_hash))
    phase = perf_counter()
    registry = load_registry()
    enabled_codes = set(db.scalars(select(RuleDefinition.code).where(RuleDefinition.enabled == 1)).all())
    timings['rules_ms'] = (perf_counter() - phase) * 1000
    phase = perf_counter()
    knowledge = list(db.scalars(select(KnowledgeDocument)).all()) if use_llm else []
    if use_llm:
        timings['retrieval_ms'] = (perf_counter() - phase) * 1000
    cache = request.app.state.analysis_cache
    cache_key = None
    if use_cache and s.analysis_cache_enabled and (use_llm or use_language_checks):
        namespace = {
            'application': app.version, 'authorization_scope': s.app_api_key,
            'request': {'filename': filename, 'document_type': document_type.value,
                        'template_id': template_id, 'policy_profile': policy_profile,
                        'external_document_id': external_document_id, 'use_llm': use_llm,
                        'use_language_checks': use_language_checks,
                        'allow_external_transfer': allow_external_transfer},
            'registry': registry.model_dump(mode='json'), 'enabled_codes': sorted(enabled_codes),
            'language': {'enabled': s.language_tool_enabled, 'endpoint': s.language_tool_base_url,
                         'timeout': s.language_tool_timeout_seconds, 'version': '6.6'},
            'model': {'provider': s.llm_provider, 'enabled': s.allow_external_llm,
                      'gateway': s.openclaw_base_url, 'agent': s.openclaw_model,
                      'gateway_credential': s.openclaw_api_key, 'backend': s.model_backend,
                      'timeout': s.llm_timeout_seconds, 'retries': s.llm_max_retries,
                      'yandex': s.yandex_model, 'gigachat': s.gigachat_model, 'zai': s.zai_model},
            'knowledge': [{'id': item.id, 'kind': item.kind, 'source_ref': item.source_ref,
                           'content': item.content} for item in sorted(knowledge, key=lambda item: item.id)],
        }
        candidate = cache.fingerprint(data, namespace)
        decision = cache.claim(candidate)
        if decision.status == 'hit':
            try:
                cached = db.get(AnalysisRun, decision.report_id)
                if cached is not None and cached.status == 'completed':
                    replayed = False
                    if key_hash:
                        cached, replayed = persist_run(db, cached, key_hash, request_hash)
                        if replayed:
                            response.headers['Idempotency-Replayed'] = 'true'
                    response.headers['Analysis-Cache'] = 'bypass' if replayed else 'hit'
                    return as_response(cached)
            except SQLAlchemyError:
                db.rollback()
                cache.discard(candidate)
                raise HTTPException(503, 'Не удалось прочитать или связать сохранённый отчёт. '
                                    'Новый тяжёлый запуск не выполнялся. Проверьте доступность БД и повторите запрос.',
                                    headers={'Retry-After': '2'}) from None
            cache.discard(candidate)
            decision = cache.claim(candidate)
        if decision.status == 'busy':
            raise HTTPException(409, 'Такой документ с теми же параметрами уже проверяется. '
                                'Дождитесь завершения и повторите запрос; после успешного завершения '
                                'может быть использован кэш. Второй тяжёлый запуск не начат.',
                                headers={'Retry-After': '2', 'Analysis-Cache': 'busy', 'Cache-Control': 'no-store'})
        if decision.status == 'miss':
            cache_key = candidate
            response.headers['Analysis-Cache'] = 'miss'
    try:
        result = await perform_analysis(request, response, db, data, filename, document_type,
            template_id, policy_profile, external_document_id, use_llm, use_language_checks,
            allow_external_transfer, key_hash, request_hash, registry, enabled_codes, knowledge, started, timings)
        if cache_key:
            replayed = response.headers.get('Idempotency-Replayed') == 'true'
            cache.finish(cache_key, result.id if result.status == 'completed' and not replayed else None)
            if replayed:
                response.headers['Analysis-Cache'] = 'bypass'
            cache_key = None
        return result
    finally:
        if cache_key:
            cache.finish(cache_key, None)


async def perform_analysis(request: Request, response: Response, db: Session, data: bytes,
                          filename: str, document_type: DocumentType, template_id: str | None,
                          policy_profile: str | None, external_document_id: str | None,
                          use_llm: bool, use_language_checks: bool, allow_external_transfer: bool,
                          key_hash: str | None,
                          request_hash: str | None, registry: RuleSet, enabled_codes: set[str],
                          knowledge: list[KnowledgeDocument], started: float,
                          timings: dict[str, float | None]) -> AnalysisResponse:
    s = get_settings()
    phase = perf_counter()
    try:
        doc = parse_docx(data)
    except DocumentParseError as exc:
        raise HTTPException(422, str(exc)) from exc
    timings["parse_ms"] = round((perf_counter() - phase) * 1000, 3)
    phase = perf_counter()
    profile = document_profile(doc)
    timings["profile_ms"] = round((perf_counter() - phase) * 1000, 3)
    phase = perf_counter()
    findings = inspect_document(doc, document_type, template_id, registry=registry, enabled_codes=enabled_codes)
    selected_rules = applicable_rules(registry, document_type, template_id, enabled_codes, policy_profile)
    policy_metrics = {"profile": None, "status": "not_requested"}
    if policy_profile:
        policy_findings, policy_metrics = evaluate_policy(doc, selected_rules)
        policy_metrics["status"] = "completed"
        findings.extend(policy_findings)
    timings["rules_ms"] = round((perf_counter() - phase) * 1000 + (timings['rules_ms'] or 0), 3)
    warnings = local_warnings(doc, policy_metrics)
    if profile["classification"] == "likely_non_letter":
        warnings.append("В начале документа найден заголовок учебной работы и минимум два характерных раздела (цель, ход работы, задание или вывод). Возможно, это учебная работа. Для проверки служебной переписки выберите исходящее письмо; текущий анализ не заблокирован.")
    mode, provider, status = "rules_only", "none", "partial" if policy_metrics.get("truncated") else "completed"
    if policy_metrics.get("truncated"):
        warnings.append("Достигнут лимит 2000 результатов или 200 замечаний профиля; проверка неполная.")
    metrics = count_metrics(findings, doc.paragraph_count)
    metrics["applied_rules"] = {rule.code: rule.version for rule in applicable_rules(
        registry, document_type, template_id, enabled_codes, policy_profile
    )}
    metrics["document_profile"] = profile
    metrics["policy_checks"] = policy_metrics
    metrics["structure"] = {"image_references": sum(p.image_count for p in doc.paragraphs),
                            "headings": sum(bool(p.heading_level) for p in doc.paragraphs),
                            "tables": len(doc.tables)}
    metrics["language_check"] = {"requested": use_language_checks, "status": "pending" if use_language_checks else "not_requested"}
    from uuid import uuid4
    run = AnalysisRun(
        id=str(uuid4()), filename=filename, mode=mode, provider=provider,
        status="processing" if use_llm or use_language_checks else status,
        findings=[x.model_dump(mode="json") for x in findings], metrics=metrics, warnings=warnings,
        external_document_id=external_document_id, request_id=request.state.request_id,
        ruleset_version=registry.version, document_type=document_type.value, template_id=template_id,
    )
    phase = perf_counter()
    run, was_replayed = persist_run(db, run, key_hash, request_hash)
    if was_replayed:
        response.headers["Idempotency-Replayed"] = "true"
        return as_response(run)
    timings["initial_save_ms"] = round((perf_counter() - phase) * 1000, 3)
    if use_language_checks:
        phase = perf_counter()
        try:
            language = await check_language(s, doc)
            findings.extend(language.findings)
            metrics["language_check"] = {"requested": True, "status": "completed",
                                         "engine": "LanguageTool", "version": language.engine_version,
                                         "checked_characters": language.checked_characters,
                                         "truncated": language.truncated, "findings": len(language.findings)}
            if language.findings:
                warnings.append(f"LanguageTool предложил замечаний: {len(language.findings)}; проверено символов: {language.checked_characters}. В findings указаны конкретные цитаты и варианты правок. Имена, сокращения и специальные термины сверьте со своим словарём перед исправлением.")
            if language.truncated:
                warnings.append(f"LanguageTool проверил первые {language.checked_characters} символов Python из {len(doc.text)} (лимит 20 000 единиц UTF-16). Оставшийся текст не проверялся на орфографию и грамматику; проверьте его отдельно.")
        except LanguageCheckError as exc:
            status = "partial"
            metrics["language_check"] = {"requested": True, "status": "partial"}
            warnings.append(str(exc))
        timings["language_ms"] = round((perf_counter() - phase) * 1000, 3)
    if use_llm:
        phase = perf_counter()
        contexts = retrieve(doc.text, knowledge)
        timings["retrieval_ms"] = round((perf_counter() - phase) * 1000 + (timings['retrieval_ms'] or 0), 3)
        phase = perf_counter()
        try:
            llm_findings = await analyze_with_llm(
                s, doc.text, contexts, allow_external_transfer=allow_external_transfer, document=doc
            )
            before_merge = len(findings)
            # Conservative deduplication by category and literal evidence.
            existing = {(x.category, x.evidence.casefold().strip()) for x in findings}
            for x in llm_findings:
                key = (x.category, x.evidence.casefold().strip())
                if key not in existing:
                    findings.append(x)
                    existing.add(key)
            metrics["llm_validation"] = {"received": getattr(llm_findings, "received", len(llm_findings)),
                                         "accepted_by_filter": len(llm_findings),
                                         "added_to_report": len(findings) - before_merge,
                                         "duplicates_with_local_findings": len(llm_findings) - (len(findings) - before_merge),
                                         "rejected_by_reason": getattr(llm_findings, "rejected", {})}
            if getattr(llm_findings, "rejected", {}):
                warnings.append("Часть замечаний модели отклонена консервативным фильтром; причины и счётчики приведены в metrics.llm_validation.")
            mode, provider = "hybrid", s.llm_provider
            warnings.append("Замечания модели требуют проверки человеком; применимость норм не подтверждена автоматически")
            if len(doc.text) > 12000:
                warnings.append("LLM проверяла только первые 12 000 символов; проверка неполная")
        except ProviderError as exc:
            mode, provider, status = "rules_fallback", s.llm_provider, "partial"
            warnings.append(str(exc))
        timings["llm_gateway_ms"] = round((perf_counter() - phase) * 1000, 3)
    run.mode, run.provider, run.status = mode, provider, status
    run.findings = [x.model_dump(mode="json") for x in findings]
    timings["total_before_final_save_ms"] = round((perf_counter() - started) * 1000, 3)
    run.metrics = {**metrics, **count_metrics(findings, doc.paragraph_count), "timings_ms": timings}
    run.warnings = warnings
    db.commit()
    db.refresh(run)
    return as_response(run)


@app.get("/api/v1/reports", response_model=list[AnalysisResponse], dependencies=[Depends(require_api_key)],
         tags=["Отчёты"], summary="Получить список сохранённых отчётов", responses=api_docs.AUTH_RESPONSE,
         description="Отчёты от новых к старым по времени создания. Возвращается массив, без нового анализа или передачи в модель. Может содержать partial и processing; пустой массив означает отсутствие записей по фильтру.")
def list_reports(limit: int = Query(20, ge=1, le=100, description="Размер страницы: 1–100 отчётов, по умолчанию 20."),
                 offset: int = Query(0, ge=0, description="Сколько отчётов пропустить для пагинации; по умолчанию 0."),
                 request_id: UUID | None = Query(None, description="Необязательный UUID исходного запроса анализа из поля request_id отчёта. Фильтр не ищет по external_document_id или ID произвольного повторного запроса."),
                 db: Session = Depends(get_db)):
    query = select(AnalysisRun)
    if request_id:
        query = query.where(AnalysisRun.request_id == str(request_id))
    records = db.scalars(query.order_by(AnalysisRun.created_at.desc()).offset(offset).limit(limit)).all()
    return [as_response(run) for run in records]


@app.get("/api/v1/reports/{report_id}", response_model=AnalysisResponse,
         dependencies=[Depends(require_api_key)], tags=["Отчёты"], summary="Прочитать сохранённый отчёт",
         responses=api_docs.NOT_FOUND_RESPONSE,
         description="Возвращает сохранённый результат по id без повторного вызова модели. У processing результаты предварительные; после прерывания автоматического перезапуска нет. Поля подробно описаны в схеме AnalysisResponse.")
def report(report_id: UUID = ApiPath(..., description="UUID из поля id ответа анализа; это не external_document_id, request_id или Idempotency-Key."), db: Session = Depends(get_db)):
    run = db.get(AnalysisRun, str(report_id))
    if not run:
        raise HTTPException(404, "Отчёт не найден")
    return as_response(run)


@app.delete("/api/v1/reports/{report_id}", status_code=204, dependencies=[Depends(require_api_key)],
            tags=["Отчёты"], summary="Удалить отчёт", responses=api_docs.NOT_FOUND_RESPONSE,
            description="Намеренно удаляет сохранённый отчёт, не исходный файл и не корпус. Успешный ответ 204 без тела. Маркер идемпотентности сохраняется: повтор POST с прежним ключом даст 410 и не вызовет модель заново.")
def delete_report(request: Request, report_id: UUID = ApiPath(..., description="UUID отчёта, который нужно намеренно удалить."), db: Session = Depends(get_db)):
    run = db.get(AnalysisRun, str(report_id))
    if not run:
        raise HTTPException(404, "Отчёт не найден")
    db.execute(update(IdempotencyRecord).where(IdempotencyRecord.analysis_id == run.id).values(analysis_id=None))
    db.execute(update(AssistantOperation).where(AssistantOperation.report_id == run.id)
               .values(report_id=None, result=None))
    db.delete(run)
    db.commit()
    request.app.state.analysis_cache.invalidate_report(str(report_id))


@app.post('/api/v1/reports/{report_id}/assistant', response_model=AssistantResponse,
          dependencies=[Depends(require_api_key)], tags=['Помощник'],
          summary='Объяснить замечание или задать вопрос по нему',
          description='Выберите finding_index из исходного массива findings. Explain/fix доступны локально без модели. '
          'Для уточнения моделью, question и rephrase нужны use_llm=true, согласие этого запроса и глобальное разрешение; '
          'обращение только через OpenClaw. Для rephrase вставьте selected_text: полного письма в отчёте нет. '
          'Основание берётся из сохранённой версии правила, не из последней конфигурации; это не цитата оригинала инструкции. '
          'Ссылки и схема ответа проверяются, для предложения контролируется сохранение чисел/контактов/ссылок, но смысл и имена проверяет человек. '
          'Для модели обязателен Idempotency-Key. Повтор не вызывает модель, согласие нужно и для повтора. '
          'С ключом в БД сохраняются HMAC, статус и ограниченный ответ; исходный вопрос/selected_text отдельно не сохраняются, '
          'но ответ может содержать цитату или переформулированный текст. Без ключа локальный ответ не сохраняется. '
          'Удаление исходного отчёта/retention удаляет связанные ответы, сохраняя обезличенные маркеры. '
          'У сбоя отдельный HTTP 502: исходный анализ остаётся доступным. Истории диалога и автоматической правки DOCX нет.',
          responses={**api_docs.AUTH_RESPONSE, 404: {'description': 'Отчёт или индекс замечания не найден.'},
                     409: {'description': 'Нет разрешения/ключа операции, несовместимое действие, другой запрос с прежним ключом либо операция уже processing.'},
                     410: {'description': 'Исходный отчёт удалён. Повтор не вызывает модель.'},
                     413: {'description': 'Контекст замечания превышает ограничение помощника.'},
                     502: {'description': 'Модельный ответ не получен/отклонён. Отчёт анализа сохранён; повтор прежней операции не вызывает модель.'},
                     503: {'description': 'Ошибка БД. После неопределённого ответа повторяйте с тем же ключом.'}})
async def finding_assistant(request: Request, response: Response,
        payload: AssistantRequest = Body(..., openapi_examples={
            'local_explain': {'summary': 'Локальное объяснение, без модели',
                              'value': {'finding_index': 0, 'action': 'explain'}},
            'local_fix': {'summary': 'Шаги исправления, без модели',
                          'value': {'finding_index': 0, 'action': 'fix'}},
            'question': {'summary': 'Вопрос модели: перед исполнением явно подтвердите передачу',
                         'value': {'finding_index': 0, 'action': 'question', 'question': 'Почему эта формулировка неоднозначна?',
                                   'use_llm': True, 'allow_external_transfer': False}},
            'rephrase': {'summary': 'Синтетический фрагмент: перед исполнением явно подтвердите передачу',
                         'value': {'finding_index': 0, 'action': 'rephrase',
                                   'selected_text': 'Просим до 24.12.2025 направить 12 экземпляров учебных материалов.',
                                   'use_llm': True, 'allow_external_transfer': False}},
        }),
        report_id: UUID = ApiPath(..., description='UUID сохранённого отчёта, в котором выбрано замечание.'),
        idempotency_key: str | None = Header(None, alias='Idempotency-Key', pattern=r'^[A-Za-z0-9_.:-]{8,128}$',
            description='Ключ операции помощника. Обязателен для use_llm=true, необязателен локально. Для повтора тех же данных сохраните ключ; новое действие/вопрос — новый ключ. Область отделена от ключей анализа.'),
        db: Session = Depends(get_db)) -> AssistantResponse:
    try:
        result, replayed = await assist_finding(db, str(report_id), payload, get_settings(),
                                               request.state.request_id, idempotency_key)
    except ProviderError as exc:
        # ProviderError is explicitly a safe public error, never an upstream body.
        raise HTTPException(502, FAILED_MESSAGE + ' Причина: ' + str(exc)) from None
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(503, 'Не удалось сохранить или прочитать операцию помощника. Повторите с прежним Idempotency-Key; новый вызов автоматически не запускается.') from None
    if replayed:
        response.headers['Idempotency-Replayed'] = 'true'
    return result


@app.get("/api/v1/rules", dependencies=[Depends(require_api_key)], tags=["Правила"],
         summary="Посмотреть текущие правила и применимость", description=api_docs.RULES_DESCRIPTION,
         responses=api_docs.AUTH_RESPONSE)
def rules(db: Session = Depends(get_db)):
    enabled = {rule.code: bool(rule.enabled) for rule in db.scalars(select(RuleDefinition)).all()}
    return [{**rule.model_dump(mode="json"), "enabled": enabled.get(rule.code, False)}
            for rule in load_registry().rules]


@app.get("/api/v1/rules/{code}/versions", dependencies=[Depends(require_api_key)], tags=["Правила"],
         summary="Посмотреть сохранённые версии правила", responses=api_docs.NOT_FOUND_RESPONSE,
         description="Массив неизменяемых спецификаций code/version с источником и применимостью. Формат элементов описан в GET /rules; поле enabled здесь не возвращается. Это версии правил, не версии ГОСТ.")
def rule_versions(code: str = ApiPath(..., description="Код правила из /rules или rule-замечания, например CONTENT_VAGUE_DEADLINE. Код, придуманный моделью, может отсутствовать в реестре."), db: Session = Depends(get_db)):
    versions = db.scalars(select(RuleVersion).where(RuleVersion.code == code).order_by(RuleVersion.version)).all()
    if not versions:
        raise HTTPException(404, "Правило не найдено")
    return [version.specification for version in versions]


@app.post("/api/v1/knowledge/documents", response_model=KnowledgeItem,
          dependencies=[Depends(require_api_key)], tags=["База знаний"], summary="Добавить разрешённый материал",
          description="Сохраняет извлечённый текст DOCX/TXT в PostgreSQL до явного удаления. Сам этот запрос не вызывает модель. Материал может попасть в найденный контекст будущего модельного анализа при двойном согласии. Ответ содержит только метаданные. Присвоение normative не подтверждает нормативный статус или применимость.",
          responses={**api_docs.AUTH_RESPONSE, 413: {"description": "Файл превышает лимит или извлечённый текст длиннее 150 000 символов."},
                     422: {"description": "Неверный kind, неподдерживаемый/повреждённый файл или нет распознанного текста."}})
async def add_knowledge(file: UploadFile = File(..., description="Разрешённый DOCX или UTF-8 TXT. По умолчанию до 5 МиБ; текст до 150 000 символов. Хранение загруженного материала должно быть разрешено владельцем."),
                        kind: SourceKind = Form(..., description="Роль: normative — нормативный материал, good — положительный образец, bad — отрицательный. Образцы не служат нормативным основанием."),
                        source_ref: str = Form("", description="Необязательный идентификатор источника/экспертной разметки. Крайние пробелы удаляются, сохраняются первые 255 символов. Может быть пустым. Попадает в контекст как ссылка, поэтому не включайте лишние приватные идентификаторы."),
                        db: Session = Depends(get_db)):
    s = get_settings()
    filename = Path(file.filename or "unknown").name[:255]
    data = await read_limited(file, s.max_document_bytes)
    try:
        content = parse_knowledge(filename, data)
    except DocumentParseError as exc:
        raise HTTPException(422, str(exc)) from exc
    if not content.strip():
        raise HTTPException(422, "Файл базы знаний не содержит текста")
    if len(content) > 150_000:
        raise HTTPException(413, "Документ базы знаний слишком большой")
    obj = KnowledgeDocument(filename=filename, kind=kind.value,
                            source_ref=source_ref.strip()[:255], content=content)
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return KnowledgeItem.model_validate(obj, from_attributes=True)


@app.get("/api/v1/knowledge/documents", response_model=list[KnowledgeItem],
         dependencies=[Depends(require_api_key)], tags=["База знаний"], summary="Получить метаданные базы знаний",
         description="Массив материалов от новых к старым. Текст документов не возвращается; модель не вызывается. Пустой список означает, что материалов пока нет.", responses=api_docs.AUTH_RESPONSE)
def list_knowledge(db: Session = Depends(get_db)):
    records = db.scalars(select(KnowledgeDocument).order_by(KnowledgeDocument.created_at.desc())).all()
    return [KnowledgeItem.model_validate(x, from_attributes=True) for x in records]


@app.delete("/api/v1/knowledge/documents/{item_id}", status_code=204,
            dependencies=[Depends(require_api_key)], tags=["База знаний"], summary="Удалить материал базы знаний",
            description="Намеренно удаляет материал и сохранённый текст из базы знаний. Ответ 204 без тела. Прежние отчёты и цитаты в них не удаляются.", responses=api_docs.NOT_FOUND_RESPONSE)
def delete_knowledge(item_id: UUID = ApiPath(..., description="UUID из id материала базы знаний; это не UUID отчёта."), db: Session = Depends(get_db)):
    item = db.get(KnowledgeDocument, str(item_id))
    if not item:
        raise HTTPException(404, "Документ базы знаний не найден")
    db.delete(item)
    db.commit()
