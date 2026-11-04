from uuid import uuid4

import pytest
from sqlalchemy import select
from test_api import document_file

from app.config import get_settings
from app.db import SessionLocal
from app.models import IdempotencyRecord


def test_metadata_trace_and_replay(client, headers):
    key = uuid4().hex
    auth = {**headers, "Idempotency-Key": key}
    form = {"external_document_id": "synthetic-sed-123", "document_type": "request", "template_id": "test-template"}
    file = document_file()
    first = client.post("/api/v1/analyze", headers=auth, files={"file": file}, data=form)
    assert first.status_code == 200, first.text
    report = first.json()
    assert report["request_id"] == first.headers["X-Request-ID"]
    assert report["external_document_id"] == form["external_document_id"]
    assert report["ruleset_version"] == "letters-0.5.0"
    assert report["metrics"]["applied_rules"]["CONTENT_VAGUE_DEADLINE"] == "1.0.1"
    second = client.post("/api/v1/analyze", headers=auth, files={"file": file}, data=form)
    assert second.status_code == 200 and second.json()["id"] == report["id"]
    assert second.headers["Idempotency-Replayed"] == "true"
    assert second.headers["X-Request-ID"] != first.headers["X-Request-ID"]
    conflict = client.post("/api/v1/analyze", headers=auth, files={"file": document_file("Другой текст")}, data=form)
    assert conflict.status_code == 409
    found = client.get("/api/v1/reports", headers=headers, params={"request_id": report["request_id"]}).json()
    assert [run["id"] for run in found] == [report["id"]]
    with SessionLocal() as db:
        records = db.scalars(select(IdempotencyRecord).where(IdempotencyRecord.analysis_id == report["id"])).all()
        assert len(records) == 1
        assert records[0].key_hash != key and len(records[0].request_hash) == 64


def test_llm_is_called_once_and_identifiers_stay_local(client, headers, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "llm_provider", "openclaw")
    monkeypatch.setattr(settings, "allow_external_llm", True)
    calls = []
    async def model(settings, text, contexts, *, allow_external_transfer, document):
        calls.append(text)
        assert allow_external_transfer
        assert "PRIVATE-SED-ID" not in text
        return []
    monkeypatch.setattr("app.main.analyze_with_llm", model)
    auth = {**headers, "Idempotency-Key": uuid4().hex}
    file = document_file()
    form = {"external_document_id": "PRIVATE-SED-ID", "use_llm": True, "allow_external_transfer": True}
    first = client.post("/api/v1/analyze", headers=auth, files={"file": file}, data=form)
    second = client.post("/api/v1/analyze", headers=auth, files={"file": file}, data=form)
    assert first.status_code == second.status_code == 200
    assert first.json()["mode"] == "hybrid"
    assert len(calls) == 1


def test_deleted_report_does_not_trigger_reanalysis(client, headers):
    auth = {**headers, "Idempotency-Key": uuid4().hex}
    file = document_file()
    response = client.post("/api/v1/analyze", headers=auth, files={"file": file})
    identifier = response.json()["id"]
    assert client.delete(f"/api/v1/reports/{identifier}", headers=headers).status_code == 204
    assert client.get(f"/api/v1/reports/{identifier}", headers=headers).status_code == 404
    assert client.post("/api/v1/analyze", headers=auth, files={"file": file}).status_code == 410


def test_processing_request_is_not_repeated(client, headers):
    from app.models import AnalysisRun
    auth = {**headers, "Idempotency-Key": uuid4().hex}
    file = document_file()
    report = client.post("/api/v1/analyze", headers=auth, files={"file": file}).json()
    with SessionLocal() as db:
        db.get(AnalysisRun, report["id"]).status = "processing"
        db.commit()
    response = client.post("/api/v1/analyze", headers=auth, files={"file": file})
    assert response.status_code == 409
    assert response.headers["Retry-After"] == "2"


@pytest.mark.parametrize("form", [{"document_type": "other"}, {"template_id": "../private"}, {"external_document_id": "x" * 129}])
def test_invalid_integration_metadata(client, headers, form):
    assert client.post("/api/v1/analyze", headers=headers, files={"file": document_file()}, data=form).status_code == 422


def test_untrusted_trace_and_invalid_key(client, headers):
    response = client.get("/health/live", headers={"X-Request-ID": "user supplied identifier"})
    assert response.headers["X-Request-ID"] != "user supplied identifier"
    assert client.post("/api/v1/analyze", headers={**headers, "Idempotency-Key": "short"},
                       files={"file": document_file()}).status_code == 422
