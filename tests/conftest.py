"""Тесты изолированы от реальной БД и внешних провайдеров."""
import os
import tempfile
from pathlib import Path
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient

DB_PATH = Path(tempfile.gettempdir()) / f"sed_dss_test_{uuid4().hex}.sqlite3"
os.environ["DATABASE_URL"] = f"sqlite:///{DB_PATH}"
os.environ["APP_API_KEY"] = "test-key"
os.environ["LLM_PROVIDER"] = "none"
os.environ["ALLOW_EXTERNAL_LLM"] = "false"
os.environ["IDEMPOTENCY_SECRET"] = "synthetic-idempotency-secret"

from app.main import app  # noqa: E402


@pytest.fixture

def client():
    with TestClient(app) as browser:
        yield browser


@pytest.fixture

def headers():
    return {"X-API-Key": "test-key"}
