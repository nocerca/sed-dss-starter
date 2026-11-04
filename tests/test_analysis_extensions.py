from uuid import uuid4

from test_api import document_file

from app.config import get_settings
from app.language import LanguageCheckError, LanguageResult
from app.schemas import Finding


def test_local_checks_work_without_llm_and_timings_are_saved(client, headers, monkeypatch):
    async def local(settings, document):
        return LanguageResult([Finding(code="LT_TEST", category="language", severity="info",
                                       message="Проверьте слово", method="language_tool", verified=False)],
                              len(document.text), False, "6.6")
    monkeypatch.setattr("app.main.check_language", local)
    response = client.post("/api/v1/analyze", headers=headers, files={"file": document_file()},
                           data={"use_language_checks": True})
    report = response.json()
    assert report["mode"] == "rules_only" and report["provider"] == "none" and report["status"] == "completed"
    assert report["metrics"]["language_check"]["status"] == "completed"
    times = report["metrics"]["timings_ms"]
    assert times["parse_ms"] >= 0 and times["language_ms"] >= 0 and times["llm_gateway_ms"] is None
    assert "Server-Timing" in response.headers
    saved = client.get(f"/api/v1/reports/{report['id']}", headers=headers).json()
    assert saved["metrics"]["timings_ms"] == times


def test_local_failure_does_not_erase_rules_or_successful_llm(client, headers, monkeypatch):
    async def failed(*args, **kwargs):
        raise LanguageCheckError("Локальный модуль недоступен")
    async def model(*args, **kwargs):
        return []
    monkeypatch.setattr("app.main.check_language", failed)
    monkeypatch.setattr("app.main.analyze_with_llm", model)
    monkeypatch.setattr(get_settings(), "llm_provider", "openclaw")
    monkeypatch.setattr(get_settings(), "allow_external_llm", True)
    result = client.post("/api/v1/analyze", headers=headers, files={"file": document_file()},
                         data={"use_language_checks": True, "use_llm": True, "allow_external_transfer": True}).json()
    assert result["status"] == "partial" and result["mode"] == "hybrid"
    assert any(f["method"] == "rule" for f in result["findings"])


def test_genre_warning_is_advisory_and_does_not_reject_file(client, headers):
    result = client.post("/api/v1/analyze", headers=headers, files={"file": document_file(
        "Лабораторная работа. Цель работы. Ход работы. Вывод."
    )}).json()
    assert result["status"] == "completed"
    assert result["metrics"]["document_profile"]["classification"] == "likely_non_letter"
    assert any("учебная работа" in warning for warning in result["warnings"])


def test_language_option_participates_in_idempotency_without_changing_default(client, headers):
    auth = {**headers, "Idempotency-Key": uuid4().hex}
    file = document_file()
    first = client.post("/api/v1/analyze", headers=auth, files={"file": file})
    repeated = client.post("/api/v1/analyze", headers=auth, files={"file": file}, data={"use_language_checks": False})
    assert first.json()["id"] == repeated.json()["id"]
    assert client.post("/api/v1/analyze", headers=auth, files={"file": file},
                       data={"use_language_checks": True}).status_code == 409


def test_disabled_local_module_returns_partial_without_network(client, headers, monkeypatch):
    monkeypatch.setattr(get_settings(), "language_tool_enabled", False)
    result = client.post("/api/v1/analyze", headers=headers, files={"file": document_file()},
                         data={"use_language_checks": True}).json()
    assert result["status"] == "partial" and result["mode"] == "rules_only"
    assert result["metrics"]["language_check"]["status"] == "partial"
    assert any(f["method"] == "rule" for f in result["findings"])
