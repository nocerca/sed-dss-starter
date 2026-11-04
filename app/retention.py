"""Explicit report retention. Dry run is the default; corpus records are never touched."""
import argparse
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.models import AnalysisRun, IdempotencyRecord


def purge_reports(db: Session, cutoff: datetime, *, execute: bool = False) -> int:
    expired = select(AnalysisRun.id).where(AnalysisRun.created_at < cutoff)
    count = db.scalar(select(func.count()).select_from(AnalysisRun).where(AnalysisRun.created_at < cutoff))
    if execute:
        db.execute(update(IdempotencyRecord).where(IdempotencyRecord.analysis_id.in_(expired)).values(analysis_id=None))
        db.execute(delete(AnalysisRun).where(AnalysisRun.created_at < cutoff))
        db.commit()
    return count or 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Очистка старых отчётов; без --execute только подсчёт")
    parser.add_argument("--older-than-days", type=int, required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if args.older_than_days < 1:
        parser.error("Период должен быть не меньше одного дня")
    from app.db import SessionLocal
    with SessionLocal() as session:
        count = purge_reports(session, datetime.now(timezone.utc) - timedelta(days=args.older_than_days), execute=args.execute)
    print(("Удалено отчётов:" if args.execute else "К удалению (изменений нет):"), count)
