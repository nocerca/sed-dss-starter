from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from app.migrations import upgrade_database


def test_fresh_and_legacy_upgrade_preserve_reports(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.sqlite3'}")
    config = Config(str(Path(__file__).resolve().parent.parent / "alembic.ini"))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0001")
        connection.execute(text("""INSERT INTO analysis_runs
            (id, filename, created_at, mode, provider, status, findings, metrics, warnings)
            VALUES ('synthetic-legacy', 'synthetic.docx', '2026-10-25', 'rules_only', 'none',
                    'completed', '[]', '{}', '[]')"""))
    upgrade_database(engine)
    upgrade_database(engine)
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT filename FROM analysis_runs WHERE id='synthetic-legacy'")) == "synthetic.docx"
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0002"
        assert connection.scalar(text("SELECT external_document_id FROM analysis_runs")) is None
    assert {"idempotency_records", "rule_versions"}.issubset(inspect(engine).get_table_names())


def test_incompatible_schema_is_not_overwritten(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'invalid.sqlite3'}")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE analysis_runs (id VARCHAR PRIMARY KEY)"))
        connection.execute(text("INSERT INTO analysis_runs VALUES ('keep-this-row')"))
    with pytest.raises(RuntimeError, match="incompatible"):
        upgrade_database(engine)
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT id FROM analysis_runs")) == "keep-this-row"
