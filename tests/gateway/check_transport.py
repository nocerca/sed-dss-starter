"""Use the production transport with synthetic data and explicit test consents."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import httpx
from smoke_test import synthetic_docx

from app.config import Settings
from app.providers import ProviderError, analyze_with_llm

settings = Settings(
    _env_file=None, llm_provider="openclaw", allow_external_llm=True,
    openclaw_api_key="test-gateway-token-not-a-real-secret-0000",
)
try:
    asyncio.run(analyze_with_llm(settings, "Несогласованный синтетический запрос", []))
    raise AssertionError("Request consent must be required")
except ProviderError:
    pass
for marker in ("FIRST_SYNTHETIC_MARKER", "SECOND_SYNTHETIC_MARKER"):
    result = asyncio.run(analyze_with_llm(
        settings, marker + " Просим направить учебные материалы.", [], allow_external_transfer=True,
    ))
    assert len(result) == 1, "Gateway did not return the synthetic finding"
    assert result[0].code == "SEMANTIC_DEMO"
    assert result[0].verified is False
assert httpx.get("http://mock-model:8002/state", trust_env=False).json()["calls"] == 2
headers = {"X-API-Key": "synthetic-api-key", "Idempotency-Key": uuid4().hex}
letter = synthetic_docx()
form = {"external_document_id": "PRIVATE-SED-ID", "document_type": "request",
        "use_llm": "true", "allow_external_transfer": "true"}


def post_report():
    return httpx.post("http://api:8000/api/v1/analyze", headers=headers, data=form,
                      files={"file": ("synthetic.docx", letter)}, timeout=90, trust_env=False)


with ThreadPoolExecutor(max_workers=2) as pool:
    responses = list(pool.map(lambda _: post_report(), range(2)))
assert all(response.status_code in {200, 409} for response in responses)
assert any(response.status_code == 200 for response in responses)
report = next(response.json() for response in responses if response.status_code == 200)
assert report["mode"] == "hybrid" and report["status"] == "completed"
assert report["external_document_id"] == "PRIVATE-SED-ID"
assert report["ruleset_version"] == "letters-0.5.0"
assert report["metrics"]["llm_validation"]["added_to_report"] == 1
assert report["metrics"]["timings_ms"]["llm_gateway_ms"] >= 0
assert post_report().json()["id"] == report["id"]
assert httpx.get("http://mock-model:8002/state", trust_env=False).json()["calls"] == 3
denied = httpx.post("http://api:8000/api/v1/analyze", headers={"X-API-Key": "synthetic-api-key"},
                    files={"file": ("synthetic.docx", letter)}, data={"use_llm": "true"}, trust_env=False)
assert denied.status_code == 409
assert httpx.get("http://mock-model:8002/state", trust_env=False).json()["calls"] == 3
assert httpx.get(f"http://api:8000/api/v1/reports/{report['id']}", headers=headers, trust_env=False).status_code == 200
assert httpx.delete(f"http://api:8000/api/v1/reports/{report['id']}", headers=headers, trust_env=False).status_code == 204
assert post_report().status_code == 410
assert httpx.get("http://mock-model:8002/state", trust_env=False).json()["calls"] == 3
print("OK: REST, PostgreSQL migrations, concurrent idempotency, gateway, consent, sessions and report deletion")
