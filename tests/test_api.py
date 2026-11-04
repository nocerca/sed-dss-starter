from io import BytesIO
from docx import Document
import pytest

from app.config import get_settings
from app.providers import ProviderError


def document_file(text="Просим в кратчайшие сроки представить сведения"):
    doc = Document()
    doc.add_paragraph(text)
    stream = BytesIO()
    doc.save(stream)
    return ("test_letter.docx", stream.getvalue(),
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document")


def test_health(client):
    assert client.get("/health/live").json() == {"status": "ok"}
    assert client.get("/health/ready").status_code == 200


def test_key_is_required(client):
    assert client.get("/api/v1/capabilities").status_code == 401


def test_analyze_and_retrieve_report(client, headers):
    response = client.post("/api/v1/analyze", headers=headers,
                           files={"file": document_file()})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["mode"] == "rules_only"
    assert data["metrics"]["quality_score"] is None
    assert any(f["code"] == "CONTENT_VAGUE_DEADLINE" for f in data["findings"])
    report = client.get(f"/api/v1/reports/{data['id']}", headers=headers)
    assert report.status_code == 200
    assert report.json()["id"] == data["id"]


def test_consent_required_to_call_llm(client, headers):
    response = client.post("/api/v1/analyze", headers=headers,
             files={"file": document_file()}, data={"use_llm": "true"})
    assert response.status_code == 409


def test_knowledge_upload_list_delete(client, headers):
    created = client.post("/api/v1/knowledge/documents", headers=headers,
        files={"file": ("good.txt", "Согласованный срок исполнения".encode(), "text/plain")},
        data={"kind": "good", "source_ref": "EXAMPLE-001"})
    assert created.status_code == 200, created.text
    item_id = created.json()["id"]
    records = client.get("/api/v1/knowledge/documents", headers=headers)
    assert any(it["id"] == item_id for it in records.json())
    deleted = client.delete(f"/api/v1/knowledge/documents/{item_id}", headers=headers)
    assert deleted.status_code == 204


def test_reject_unsupported_format(client, headers):
    response = client.post("/api/v1/analyze", headers=headers,
           files={"file": ("test.pdf", b"%PDF-", "application/pdf")})
    assert response.status_code == 415


def test_gateway_failure_preserves_and_saves_rule_results(client, headers, monkeypatch):
    monkeypatch.setattr(get_settings(), "llm_provider", "openclaw")
    monkeypatch.setattr(get_settings(), "allow_external_llm", True)
    async def fail(*args, **kwargs):
        assert kwargs["allow_external_transfer"] is True
        raise ProviderError("OpenClaw недоступен: HTTP 503")
    monkeypatch.setattr("app.main.analyze_with_llm", fail)
    response = client.post("/api/v1/analyze", headers=headers, files={"file": document_file()},
                           data={"use_llm": True, "allow_external_transfer": True})
    assert response.status_code == 200
    report = response.json()
    assert report["status"] == "partial"
    assert report["mode"] == "rules_fallback"
    assert any(item["code"] == "CONTENT_VAGUE_DEADLINE" for item in report["findings"])
    assert client.get(f"/api/v1/reports/{report['id']}", headers=headers).json()["status"] == "partial"


@pytest.mark.parametrize("global_consent,request_consent", [(False, False), (False, True), (True, False)])
def test_api_privacy_guard(client, headers, monkeypatch, global_consent, request_consent):
    monkeypatch.setattr(get_settings(), "llm_provider", "openclaw")
    monkeypatch.setattr(get_settings(), "allow_external_llm", global_consent)
    async def forbidden(*args, **kwargs):
        pytest.fail("Передача контекста запрещена")
    monkeypatch.setattr("app.main.analyze_with_llm", forbidden)
    response = client.post("/api/v1/analyze", headers=headers, files={"file": document_file()},
                           data={"use_llm": True, "allow_external_transfer": request_consent})
    assert response.status_code == 409


def test_empty_and_oversized_upload(client, headers, monkeypatch):
    assert client.post("/api/v1/analyze", headers=headers,
                       files={"file": ("empty.docx", b"")}).status_code == 422
    monkeypatch.setattr(get_settings(), "max_document_bytes", 10)
    assert client.post("/api/v1/analyze", headers=headers,
                       files={"file": ("big.docx", b"x" * 11)}).status_code == 413
