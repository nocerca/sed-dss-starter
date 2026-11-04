"""Opaque request fingerprints; never store a raw idempotency key or letter hash."""
import hashlib
import hmac
import json

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import AnalysisRun, IdempotencyRecord


def fingerprints(secret: str, key: str, data: bytes, options: dict) -> tuple[str, str]:
    if len(secret) < 24:
        raise HTTPException(409, "Для идемпотентности нужен настроенный IDEMPOTENCY_SECRET")
    secret_bytes = secret.encode()
    key_hash = hmac.new(secret_bytes, b"key\0" + key.encode(), hashlib.sha256).hexdigest()
    request_hash = hmac.new(secret_bytes, b"request\0", hashlib.sha256)
    request_hash.update(json.dumps(options, ensure_ascii=False, sort_keys=True).encode())
    request_hash.update(b"\0")
    request_hash.update(data)
    return key_hash, request_hash.hexdigest()


def replay(db: Session, record: IdempotencyRecord, request_hash: str) -> AnalysisRun:
    if not hmac.compare_digest(record.request_hash, request_hash):
        raise HTTPException(409, "Этот Idempotency-Key уже использован с другим запросом")
    if record.analysis_id is None:
        raise HTTPException(410, "Отчёт удалён; повторный анализ по прежнему ключу не выполняется")
    run = db.get(AnalysisRun, record.analysis_id)
    if run is None:
        raise HTTPException(410, "Отчёт недоступен; повторная отправка не выполняется")
    if run.status == "processing":
        raise HTTPException(409, "Анализ уже начат; повторная передача не выполняется", headers={"Retry-After": "2"})
    return run


def persist_run(
    db: Session, run: AnalysisRun, key_hash: str | None, request_hash: str | None,
) -> tuple[AnalysisRun, bool]:
    db.add(run)
    try:
        db.flush()
        if key_hash:
            db.add(IdempotencyRecord(key_hash=key_hash, request_hash=request_hash, analysis_id=run.id))
        db.commit()
    except IntegrityError:
        db.rollback()
        if key_hash and (record := db.get(IdempotencyRecord, key_hash)):
            return replay(db, record, request_hash), True
        raise
    db.refresh(run)
    return run, False
