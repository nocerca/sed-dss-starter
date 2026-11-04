"""Build portable Postman imports with empty secrets and a synthetic DOCX."""

import argparse
import json
from pathlib import Path

if __package__:
    from .smoke_test import synthetic_docx
else:
    from smoke_test import synthetic_docx

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"


def event(listen: str, code: str) -> dict:
    return {"listen": listen, "script": {"type": "text/javascript", "exec": code.splitlines()}}


def request(name: str, method: str, path: str, *, description: str = "",
            status: int | None = 200, script: str = "", before: str = "",
            form: list[dict] | None = None, headers: list[dict] | None = None,
            public: bool = False) -> dict:
    result = {"name": name, "request": {
        "method": method, "header": headers or [], "url": "{{base_url}}" + path,
        "description": description,
    }, "event": [event("test", (f'pm.test("HTTP {status}", () => pm.response.to.have.status({status}));\n' if status is not None else "") + script)]}
    if public:
        result["request"]["auth"] = {"type": "noauth"}
    if form:
        result["request"]["body"] = {"mode": "formdata", "formdata": form}
    if before:
        result["event"].insert(0, event("prerequest", before))
    return result


def analysis_form(*, external: bool = False, language: bool = False) -> list[dict]:
    fields = {
        "use_llm": "true" if external else "false",
        "allow_external_transfer": "{{external_consent}}" if external else "false",
        "document_type": "unspecified", "external_document_id": "postman-synthetic-demo",
        "use_language_checks": "true" if language else "false",
    }
    return [{"key": "file", "type": "file", "src": "examples/postman_synthetic.docx"}] + [
        {"key": key, "value": value, "type": "text", **({"disabled": True} if key == "external_document_id" else {})} for key, value in fields.items()
    ]


def build_collection() -> dict:
    capture = '''const report = pm.response.json();
pm.test("Отчёт сохранён", () => pm.expect(report.id).to.be.a("string"));
pm.test("Есть трассировка", () => pm.expect(pm.response.headers.get("X-Request-ID")).to.be.a("string"));
if (pm.response.code === 200 && report.id) {
  pm.collectionVariables.set("report_id", report.id);
  pm.collectionVariables.set("request_id", report.request_id);
}'''
    local_script = capture + '''
pm.test("Локальный анализ завершён", () => {
  pm.expect(report.mode).to.eql("rules_only");
  pm.expect(report.status).to.eql("completed");
});
if (report.id) pm.collectionVariables.set("rules_report_id", report.id);'''
    same_body = analysis_form()
    no_consent = analysis_form(external=True)
    no_consent[2]["value"] = "false"
    items = [
        {"name": "01 — Состояние сервиса", "item": [
            request("Живость API", "GET", "/health/live", public=True),
            request("Готовность БД", "GET", "/health/ready", public=True),
            request("Возможности и настройки", "GET", "/api/v1/capabilities"),
        ]},
        {"name": "02 — Анализ без модели", "item": [
            request("Проверить DOCX локально", "POST", "/api/v1/analyze", form=same_body,
                    headers=[{"key": "Idempotency-Key", "value": "{{rules_idempotency_key}}"}],
                    before='pm.collectionVariables.set("rules_idempotency_key", pm.variables.replaceIn("{{$guid}}"));',
                    script=local_script,
                    description="Выберите синтетический DOCX или свой разрешённый файл. Внешней передачи нет. Новый Send создаёт новый ключ."),
            request("Повторить тот же запрос без нового анализа", "POST", "/api/v1/analyze",
                    form=analysis_form(), headers=[{"key": "Idempotency-Key", "value": "{{rules_idempotency_key}}"}],
                    script='''pm.test("Вернулся прежний отчёт", () => {
  pm.expect(pm.response.json().id).to.eql(pm.collectionVariables.get("rules_report_id"));
  pm.expect(pm.response.headers.get("Idempotency-Replayed")).to.eql("true");
});''', description="Сначала выполните предыдущий запрос. Файл и все поля должны быть идентичны."),
            request("LLM без согласия: ожидается отказ", "POST", "/api/v1/analyze",
                    form=no_consent, status=409,
                    description="Проверяет отказ до передачи текста. Модель не вызывается."),
            request("Локальные орфография и грамматика", "POST", "/api/v1/analyze",
                    form=analysis_form(language=True), script=capture + '''
pm.test("Локальный языковой модуль завершился", () => {
  pm.expect(report.metrics.language_check.status).to.eql("completed");
  pm.expect(report.provider).to.eql("none");
});''', description="Нужны профиль Docker language и LANGUAGE_TOOL_ENABLED=true. Текст проверяется внутри Docker без внешней модели. Если модуль недоступен, правила сохраняются и status=partial."),
        ]},
        {"name": "03 — Модель через OpenClaw (явное согласие)", "item": [
            request("Проверить DOCX с моделью", "POST", "/api/v1/analyze", form=analysis_form(external=True),
                    headers=[{"key": "Idempotency-Key", "value": "{{llm_idempotency_key}}"}],
                    status=None,
                    before='''const consent = pm.environment.get("external_consent") === "true";
pm.variables.set("external_consent", consent ? "true" : "false");
pm.environment.set("external_consent", "false");
pm.collectionVariables.set("llm_idempotency_key", pm.variables.replaceIn("{{$guid}}"));''',
                    script='''if (pm.variables.get("external_consent") !== "true") {
  pm.test("Без согласия сервер возвращает HTTP 409", () => pm.response.to.have.status(409));
} else {
  pm.test("HTTP 200", () => pm.response.to.have.status(200));
''' + capture + '''
pm.test("Модельная часть завершилась", () => {
  pm.expect(report.mode).to.eql("hybrid");
  pm.expect(report.provider).to.eql("openclaw");
  pm.expect(report.status).to.eql("completed");
});
pm.test("Выводы модели требуют проверки", () => {
  (report.findings || []).filter(f => f.method === "llm").forEach(f => pm.expect(f.verified).to.eql(false));
});
}''', description="Перед каждым запуском установите external_consent=true в окружении. Согласие потребляется до отправки и сбрасывается даже при таймауте. Без согласия запрос даёт понятный HTTP 409, а текст модели не передаётся. Начните с examples/postman_synthetic.docx. HTTP 200 со status=partial не означает успех модели."),
        ]},
        {"name": "04 — Отчёты", "item": [
            request("Получить последний созданный отчёт", "GET", "/api/v1/reports/{{report_id}}"),
            request("Список отчётов", "GET", "/api/v1/reports?limit=20&offset=0"),
            request("Найти по идентификатору запроса", "GET", "/api/v1/reports?request_id={{request_id}}"),
            request("Удалить выбранный отчёт (вручную)", "DELETE", "/api/v1/reports/{{report_id}}",
                    status=204, before='''if (pm.environment.get("allow_delete") !== "true") pm.execution.skipRequest();''',
                    script='pm.environment.set("allow_delete", "false");',
                    description="Проверьте report_id. Только для намеренного удаления поставьте allow_delete=true. Флаг сбрасывается после ответа. Корпус не затрагивается."),
        ]},
        {"name": "05 — Правила и база знаний", "item": [
            request("Текущие правила", "GET", "/api/v1/rules"),
            request("Версии правила о сроке", "GET", "/api/v1/rules/CONTENT_VAGUE_DEADLINE/versions"),
            request("Список документов базы знаний", "GET", "/api/v1/knowledge/documents"),
        ]},
    ]
    policy_cases = [
        ("01_left_margin", "ORG_MARGINS"), ("02_body_font", "ORG_BODY_FONT"),
        ("03_body_size", "ORG_BODY_FONT_SIZE"), ("04_body_indent", "ORG_BODY_FIRST_LINE"),
        ("05_body_spacing", "ORG_BODY_LINE_SPACING"), ("06_recipient_size", "ORG_RECIPIENT_FONT"),
        ("07_executor_phone", "ORG_EXECUTOR_CONTACT"), ("08_executor_size", "ORG_EXECUTOR_FONT"),
        ("09_date_format", "ORG_DATE_FORMAT"), ("10_signature_font", "ORG_SIGNATURE_FONT"),
    ]
    policy_items = []
    for filename, code in policy_cases:
        form = analysis_form()
        form[0]["src"] = f"examples/private/postman_cases/incorrect/{filename}.docx"
        form.append({"key": "policy_profile", "type": "text", "value": "office_instruction_letters"})
        policy_items.append(request(
            filename + " — " + code, "POST", "/api/v1/analyze", form=form,
            headers=[{"key": "Idempotency-Key", "value": "{{policy_idempotency_key}}"}],
            before='pm.collectionVariables.set("policy_idempotency_key", pm.variables.replaceIn("{{$guid}}"));',
            script=capture + '\npm.test("Обнаружен ожидаемый дефект", () => {\n'
            + f'  pm.expect(report.findings.map(f => f.code)).to.include("{code}");\n'
            + '  pm.expect(report.provider).to.eql("none");\n'
            + '  pm.expect(report.status).to.eql("completed");\n});',
            description="Синтетическое письмо с одной контролируемой правкой. Создать файлы: python scripts/create_letter_demos.py. Модель не вызывается. Если файл не найден, выберите его вручную в Body → file.",
        ))
    items.append({"name": "06 — Проверки по инструкции: 10 документов", "item": policy_items})
    return {
        "info": {"name": "SED-DSS — локальный API v1", "schema": SCHEMA,
                 "description": "Импортируйте также SED-DSS.local.postman_environment.json. Заполните только app_api_key из .env. Ключ Z.AI и токен OpenClaw сюда не добавлять. Рабочая папка Postman — корень проекта; либо выберите DOCX в Body → form-data → file. Инструкция: docs/POSTMAN.md. Без внешнего согласия сервер даёт HTTP 409. Удаляющий запрос по умолчанию пропускается. Локальный язык включается отдельно через профиль language."},
        "auth": {"type": "apikey", "apikey": [
            {"key": "key", "value": "X-API-Key", "type": "string"},
            {"key": "value", "value": "{{app_api_key}}", "type": "string"},
            {"key": "in", "value": "header", "type": "string"},
        ]},
        "variable": [{"key": key, "value": "", "type": "string"} for key in
                     ("report_id", "request_id", "rules_report_id", "rules_idempotency_key", "llm_idempotency_key", "policy_idempotency_key")],
        "item": items,
    }


def build_environment() -> dict:
    return {"name": "SED-DSS — localhost", "_postman_variable_scope": "environment",
            "values": [{"key": key, "value": value, "type": kind, "enabled": True}
                       for key, value, kind in [
                           ("base_url", "http://127.0.0.1:8000", "default"),
                           ("app_api_key", "", "secret"),
                           ("external_consent", "false", "default"),
                           ("allow_delete", "false", "default"),
                       ]]}


def export(root: Path = ROOT) -> None:
    target = root / "postman"
    target.mkdir(exist_ok=True)
    for name, data in [("SED-DSS.postman_collection.json", build_collection()),
                       ("SED-DSS.local.postman_environment.json", build_environment())]:
        (target / name).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    example = root / "examples" / "postman_synthetic.docx"
    example.parent.mkdir(exist_ok=True)
    if not example.exists():
        example.write_bytes(synthetic_docx())


if __name__ == "__main__":
    argparse.ArgumentParser(description="Создать импорт Postman без секретов").parse_args()
    export()
    print("Созданы коллекция, пустой шаблон окружения и синтетический DOCX. Секреты не читались.")
