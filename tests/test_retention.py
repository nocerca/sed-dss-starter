from datetime import datetime, timezone
from uuid import uuid4

from app.db import SessionLocal
from app.models import AnalysisRun, IdempotencyRecord, KnowledgeDocument
from app.retention import purge_reports


def test_retention_is_dry_run_and_preserves_corpus_and_idempotency(client):
    identifier = str(uuid4())
    key_hash = uuid4().hex * 2
    with SessionLocal() as db:
        db.add(AnalysisRun(id=identifier, filename="synthetic-expired.docx", created_at=datetime(2000, 1, 1),
                           mode="rules_only", provider="none", status="completed", findings=[], metrics={}, warnings=[]))
        db.flush()
        db.add(IdempotencyRecord(key_hash=key_hash, request_hash="0" * 64, analysis_id=identifier))
        source = KnowledgeDocument(filename="synthetic-retained.txt", kind="good", source_ref="DEMO", content="Синтетический пример")
        db.add(source)
        db.commit()
        source_id = source.id
        cutoff = datetime(2001, 1, 1, tzinfo=timezone.utc)
        assert purge_reports(db, cutoff) == 1
        assert db.get(AnalysisRun, identifier) is not None
        assert purge_reports(db, cutoff, execute=True) == 1
        db.expire_all()
        assert db.get(AnalysisRun, identifier) is None
        assert db.get(IdempotencyRecord, key_hash).analysis_id is None
        assert db.get(KnowledgeDocument, source_id).content == "Синтетический пример"
