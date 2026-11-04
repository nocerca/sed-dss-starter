from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class SourceKind(str, Enum):
    """Роль материала: normative — заявленный нормативный источник; good — положительный
    образец; bad — отрицательный образец. Метка не подтверждает правомерность, нормативный
    статус или применимость текста. Образцы good/bad не являются нормативными основаниями.
    """

    normative = "normative"
    good = "good"
    bad = "bad"


class DocumentType(str, Enum):
    """Заявленный тип исходящего письма: unspecified — не указан; request — запрос;
    response — ответ; notification — уведомление; cover_letter — сопроводительное письмо.
    Задаётся клиентом для применимости правил, автоматически по содержимому не определяется.
    """

    unspecified = "unspecified"
    request = "request"
    response = "response"
    notification = "notification"
    cover_letter = "cover_letter"


class Finding(BaseModel):
    """Отдельное рекомендательное замечание. Наличие цитаты не доказывает вывод модели."""

    code: str = Field(min_length=1, max_length=90, description="Код типа замечания. Для rule — код реестра; для llm код предлагает модель, он не является стабильным нормативным классификатором.", examples=["CONTENT_VAGUE_DEADLINE"])
    category: str = Field(pattern="^(language|content|formatting)$", description="Категория: language — язык, content — содержание, formatting — оформление. Категория не означает полноту проверки этого направления.")
    severity: str = Field(pattern="^(info|warning|critical)$", description="Предварительная критичность: info — информация, warning — обратить внимание, critical — проверить в первую очередь. Это не юридическая или экспертная оценка риска.")
    message: str = Field(min_length=3, max_length=1000, description="Объяснение возможного недостатка на русском языке.")
    evidence: str = Field(default="", max_length=600, description="Цитата из письма либо структурное доказательство (например, список шрифтов). Для LLM проверяется буквальное наличие цитаты в переданном фрагменте; это не доказывает правильность замечания. Может быть пустым для правила пустого документа.")
    recommendation: str = Field(default="", max_length=1000, description="Предлагаемое действие для автора. Автоматически к оригиналу не применяется.")
    expected: dict | None = Field(default=None, description="Ожидаемые свойства для локальной проверки по инструкции; null для прежних эвристик и модели.")
    observed: dict | None = Field(default=None, description="Наблюдаемые свойства DOCX, на которых основано замечание; не результат визуального рендеринга.")
    applicability_reason: str | None = Field(default=None, description="Почему проверка применена к этому блоку; распознанная роль требует проверки человеком.")
    source_ref: str | None = Field(default=None, description="Идентификатор основания или null. LOCAL-* — локальная эвристика. У модели сохраняется только ссылка из переданного нормативного контекста; существование ссылки не подтверждает применимость нормы.")
    method: str = Field(default="rule", pattern="^(rule|llm|language_tool)$", description="Источник: rule — локальная проверка (эвристика или явно выбранный профиль инструкции); llm — модель через OpenClaw; language_tool — отдельный локальный языковой модуль. Все рекомендации проверяет человек.")
    verified: bool = Field(default=True, description="Для rule значение true подтверждает выполнение условия эвристики, а не соответствие ГОСТ. Для llm и language_tool всегда false: замечание требует проверки человеком. Это не вероятность и не оценка уверенности.")
    fragment_id: str | None = Field(default=None, description="Структурный адрес фрагмента DOCX, когда известен; null при неоднозначном или неизвестном адресе. У llm и language_tool заполняется при единственном совпадении цитаты. Это не номер страницы или координаты.", examples=["body/p[0]", None])
    rule_version: str | None = Field(default=None, description="Версия спецификации для rule; версия движка (6.6) для language_tool. У модельных замечаний null.", examples=["1.0.0", "6.6", None])


class AnalysisResponse(BaseModel):
    """Сохранённый отчёт проверки, включая её ограничения. Решение принимает человек."""

    id: str = Field(description="UUID отчёта, созданный сервисом. Используется в /api/v1/reports/{report_id}. При идемпотентном повторе остаётся прежним.")
    filename: str = Field(description="Имя загруженного DOCX без пути, максимум 255 символов. Оригинальный файл не изменяется. Само имя не включается в модельный запрос.", examples=["synthetic_letter.docx"])
    mode: str = Field(description="Как выполнялась проверка: rules_only — локальная обработка без LLM, включая LanguageTool по отдельному флагу; hybrid — локальные проверки и завершённая модельная обработка; rules_fallback — локальные результаты после ошибки LLM. hybrid не требует наличия модельных замечаний и не гарантирует их достоверность.", examples=["rules_only", "hybrid", "rules_fallback"])
    provider: str = Field(description="LLM-транспорт этого отчёта: none — без модели; openclaw — обращение через обязательный шлюз, в том числе при его ошибке. Это не имя внешнего backend и не подтверждение его успешного ответа.", examples=["none", "openclaw"])
    status: str = Field(description="Чем закончилась обработка: completed — запрошенные части завершены; partial — LLM или запрошенный локальный языковой модуль не завершил проверку, доступные результаты сохранены; processing — результат ещё не зафиксирован (может остаться после прерывания). completed не означает правильность документа или подтверждённое качество анализа.", examples=["completed", "partial", "processing"])
    findings: list[Finding] = Field(description="Список замечаний правил и/или модели. Пустой список означает отсутствие принятых замечаний в выполненных проверках, а не доказанную безошибочность письма.")
    metrics: dict = Field(description="Счётчики замечаний и сведения о выполнении правил. Это не проценты качества и не экспертная оценка. Отсутствующие категории в словарях имеют нулевой счётчик. У старых отчётов applied_rules может отсутствовать.", json_schema_extra={"properties": {
        "total_findings": {"type": "integer", "description": "Всего возвращённых замечаний после проверки и объединения; равно длине findings.", "example": 1},
        "by_category": {"type": "object", "additionalProperties": {"type": "integer"}, "description": "Число замечаний по категориям language/content/formatting. Отсутствующий ключ означает 0.", "example": {"content": 1}},
        "by_severity": {"type": "object", "additionalProperties": {"type": "integer"}, "description": "Число замечаний по критичности info/warning/critical. Отсутствующий ключ означает 0.", "example": {"warning": 1}},
        "paragraph_count": {"type": "integer", "description": "Число абзацев верхнего уровня основной части DOCX. Не включает абзацы таблиц и колонтитулов; не является числом страниц.", "example": 1},
        "quality_score": {"type": "null", "description": "Всегда null: общий балл качества не рассчитывается без обоснованных весов и экспертной валидации."},
        "applied_rules": {"type": "object", "additionalProperties": {"type": "string"}, "description": "Код → версия каждого включённого применимого правила. Наличие здесь означает выполнение проверки, а не обнаруженное нарушение. Может отсутствовать в прежних отчётах.", "example": {"CONTENT_VAGUE_DEADLINE": "1.0.0"}},
        "document_profile": {"type": "object", "description": "Локальная эвристика версии 1.0.0: classification=likely_letter/likely_non_letter/undetermined и signals — коды наблюдаемых признаков. Это предупреждение о применимости, не надёжная классификация и не оценка вероятности."},
        "policy_checks": {"type": "object", "description": "Выбранный профиль инструкции и результаты реализованных проверок: results содержит code, outcome=pass/violation/insufficient_data, fragment_id, expected, observed, reason, source_ref. counts — число исходов, roles — основания выделения блоков. Без выбора профиля status=not_requested. pass относится только к проверенным свойствам; визуальная раскладка и полный состав реквизитов не подтверждаются."},
        "structure": {"type": "object", "description": "image_references — ссылки на изображения в распознанных абзацах OOXML (не уникальные файлы); headings — абзацы с уровнем заголовка Word; tables — распознанные таблицы, включая вложенные. Содержимое картинок не распознаётся."},
        "language_check": {"type": "object", "description": "requested — запрашивалась ли локальная проверка; status=not_requested/completed/partial/pending. При успехе: engine, version, checked_characters (символы Python), truncated, findings. Ограничение запроса — 20 000 единиц UTF-16; внешние сервисы не используются."},
        "llm_validation": {"type": "object", "description": "received — предложений от LLM; accepted_by_filter — прошло фильтр; added_to_report — добавлено после сравнения с локальными замечаниями; duplicates_with_local_findings — исключённые повторы. rejected_by_reason содержит коды/счётчики без отвергнутых текстов. Фильтр консервативный и не доказывает истинность принятых выводов."},
        "timings_ms": {"type": "object", "additionalProperties": {"type": ["number", "null"]}, "description": "Реальные длительности этапов в мс: parse_ms, profile_ms, rules_ms (включая загрузку правил), language_ms, retrieval_ms, llm_gateway_ms (весь вызов шлюза и разбор ответа), initial_save_ms. null — этап не выполнялся. total_before_final_save_ms измеряется до последней записи БД; сетевую передачу и сериализацию не включает. В повторе сохраняются прежние замеры; Server-Timing относится к текущему запросу."},
    }})
    warnings: list[str] = Field(default=[], description="Ограничения извлечения DOCX, проверки и сведения о сбое модели. Читать даже при completed. Изображения и визуальный вид страниц не передаются текстовой модели.")
    created_at: datetime | None = Field(default=None, description="Время создания предварительного отчёта сервером (UTC), а не время окончания анализа. Не используется как измерение задержки.")
    external_document_id: str | None = Field(default=None, description="Переданный идентификатор документа во внешней СЭД, либо null. Хранится локально для связи с системой; сам по себе не является ключом идемпотентности и не включается в модельный запрос.")
    request_id: str | None = Field(default=None, description="UUID первоначального HTTP-запроса анализа. При повторе остаётся прежним, тогда как заголовок X-Request-ID относится к текущему HTTP-запросу. В старых отчётах может быть null.")
    ruleset_version: str | None = Field(default=None, description="Версия реестра правил на момент анализа. Это не версия ГОСТ, API или модели. В старых отчётах может быть null.", examples=["demo-0.3.0"])
    document_type: DocumentType | None = Field(default=None, description="Тип письма, заявленный клиентом. Влияет на применимость правил; соответствие содержимого этому типу автоматически не проверено. В старых отчётах может быть null.")
    template_id: str | None = Field(default=None, description="Заявленный идентификатор корпоративного шаблона или null. Используется для применимости правил, а не для автоматического распознавания или проверки визуального шаблона.")


class KnowledgeItem(BaseModel):
    """Метаданные сохранённого материала базы знаний; текст в этом ответе не выдаётся."""

    id: str = Field(description="UUID документа базы знаний. Используется для явного удаления.")
    filename: str = Field(description="Имя загруженного DOCX/TXT.")
    kind: SourceKind = Field(description="Заявленная роль: нормативный материал, положительный или отрицательный образец. Загрузка не подтверждает нормативный статус.")
    source_ref: str = Field(description="Переданный идентификатор основания/экспертной разметки; может быть пустым. В контексте модели используется как ссылка на источник.")
    created_at: datetime = Field(description="Время сохранения в базе знаний, UTC. Текст хранится до явного удаления.")


class LLMFinding(BaseModel):
    claim_type: Literal["text_issue", "missing_content", "visual_issue", "normative_requirement"] = "text_issue"
    code: str = Field(default="SEMANTIC", min_length=1, max_length=90)
    category: str = Field(pattern="^(language|content|formatting)$")
    severity: str = Field(pattern="^(info|warning|critical)$")
    message: str = Field(min_length=3, max_length=1000)
    evidence: str = Field(min_length=3, max_length=400)
    recommendation: str = Field(max_length=1000)
    source_ref: str | None = None


class LLMFindings(BaseModel):
    findings: list[LLMFinding] = Field(max_length=12)
