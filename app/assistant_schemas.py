"""Bounded requests and advisory responses for one selected finding."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

AssistantAction = Literal['explain', 'fix', 'rephrase', 'question']
ShortStep = Annotated[str, Field(min_length=1, max_length=500)]


class AssistantRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)

    finding_index: int = Field(ge=0, le=5000, description='Индекс замечания в массиве findings сохранённого отчёта, с нуля. Порядок берётся из API, не из отфильтрованного списка интерфейса.')
    action: AssistantAction = Field(default='explain', description='explain — объяснить; fix — порядок исправления; rephrase — предложить формулировку selected_text; question — ответить на question в контексте этого замечания.')
    use_llm: bool = Field(default=False, strict=True, description='false — локальный ответ explain/fix по сохранённой версии правила. true — модель через OpenClaw. Для question/rephrase требуется true. JSON boolean, не строка.')
    allow_external_transfer: bool = Field(default=False, strict=True, description='Явное согласие только этого запроса на передачу вопроса, выбранного замечания, формализованного основания и selected_text через OpenClaw. Нужен также глобальный флаг. Проверяется и при идемпотентном повторе.')
    question: str | None = Field(default=None, min_length=3, max_length=1000, description='Самостоятельный вопрос для action=question, 3–1000 символов после обрезки крайних пробелов. История диалога не передаётся; сведения о других замечаниях/полном письме недоступны.')
    selected_text: str | None = Field(default=None, min_length=3, max_length=2000, description='Выбранный пользователем текст для переформулировки (обязателен для rephrase), либо дополнительный контекст question. Полный DOCX не восстанавливается из отчёта. До 2000 символов; поле не сохраняется отдельно.')

    @model_validator(mode='after')
    def validate_action(self) -> 'AssistantRequest':
        if self.action == 'question' and self.question is None:
            raise ValueError('Для question требуется непустой вопрос')
        if self.action != 'question' and self.question is not None:
            raise ValueError('question допустим только для действия question')
        if self.action == 'rephrase' and self.selected_text is None:
            raise ValueError('Для rephrase вставьте выбранный текст')
        if self.action not in {'question', 'rephrase'} and self.selected_text is not None:
            raise ValueError('selected_text допустим только для question/rephrase')
        return self


class AssistantBasis(BaseModel):
    code: str = Field(description='Код локального правила выбранного замечания.')
    version: str = Field(description='Сохранённая версия спецификации на момент проверки, а не последняя версия из конфигурации.')
    source_ref: str = Field(description='Ссылка-идентификатор основания из этой спецификации. Это не автоматически опубликованный оригинал инструкции.')
    source_title: str = Field(description='Название источника из сохранённой спецификации.')
    kind: str = Field(description='instruction — предоставленная инструкция; heuristic — локальная эвристика. Не подтверждает нормативную обязательность.')
    requirement: str = Field(description='Формализованное требование/описание правила. Не выдаётся за дословную цитату оригинала инструкции.')
    applicability: str = Field(description='Сохранённые условия применения правила.')
    exceptions: str | None = Field(default=None, description='Известные исключения из спецификации, либо null.')
    verified: bool = Field(default=False, description='Подтверждённость источника из спецификации, не достоверность ответа модели. Предоставленная инструкция пока не подтверждена экспертом.')


class AssistantResponse(BaseModel):
    report_id: str = Field(description='UUID исходного отчёта; сам отчёт и DOCX не изменяются.')
    finding_index: int = Field(description='Индекс выбранного замечания в исходном массиве findings.')
    action: AssistantAction = Field(description='Запрошенное действие помощника.')
    status: Literal['completed', 'insufficient_data'] = Field(description='completed — ответ сформирован, не гарантия его истинности; insufficient_data — данных для выбранного вопроса недостаточно.')
    method: Literal['local', 'llm'] = Field(description='local — шаблон по сохранённым фактам/спецификации; llm — рекомендация модели через OpenClaw.')
    provider: Literal['none', 'openclaw'] = Field(description='Маршрут модельного обращения, не имя внешнего backend.')
    answer: str = Field(description='Объяснение обычным текстом. HTML/Markdown не исполняются, ответ требует проверки человеком.')
    steps: list[str] = Field(default_factory=list, description='Предлагаемые действия для автора. Не выполняются сервисом автоматически.')
    suggestion: str | None = Field(default=None, description='Предлагаемая формулировка только для rephrase. Сравните смысл, имена и факты с оригиналом перед ручным использованием.')
    sources: list[AssistantBasis] = Field(default_factory=list, description='Только существующие сохранённые спецификации, использованные в ответе. Ссылки модели проверяются по разрешённому списку; наличие ссылки не доказывает применимость нормы.')
    warnings: list[str] = Field(default_factory=list, description='Ограничения контекста, источников и проверки ответа. Читать даже при completed.')
    request_id: str = Field(description='UUID исходного HTTP-запроса помощника. При повторе сохраняется; X-Request-ID относится к текущему HTTP-запросу.')


class AssistantModelOutput(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)

    status: Literal['completed', 'insufficient_data']
    answer: str = Field(min_length=3, max_length=4000)
    steps: list[ShortStep] = Field(default_factory=list, max_length=6)
    suggestion: str | None = Field(default=None, min_length=3, max_length=2000)
    source_refs: list[Annotated[str, Field(min_length=1, max_length=255)]] = Field(default_factory=list, max_length=3)
