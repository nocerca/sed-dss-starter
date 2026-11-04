"""Validate the user-facing API guide, examples and unchanged privacy defaults."""

import json

from test_api import document_file

from app.config import get_settings
from app.schemas import AnalysisResponse


def analyze_body(schema):
    operation = schema["paths"]["/api/v1/analyze"]["post"]
    reference = operation["requestBody"]["content"]["multipart/form-data"]["schema"]["$ref"]
    return schema["components"]["schemas"][reference.rsplit("/", 1)[-1]]


def test_all_request_parameters_have_help(client):
    schema = client.get("/openapi.json").json()
    for operations in schema["paths"].values():
        for operation in operations.values():
            assert operation.get("summary") and operation.get("description")
            assert operation.get("tags")
            for parameter in operation.get("parameters", []):
                assert parameter.get("description"), parameter["name"]
            for content in operation.get("requestBody", {}).get("content", {}).values():
                reference = content["schema"]["$ref"]
                body = schema["components"]["schemas"][reference.rsplit("/", 1)[-1]]
                assert all(field.get("description") for field in body["properties"].values())
    security = schema["components"]["securitySchemes"]["APIKeyHeader"]
    assert security["name"] == "X-API-Key"
    assert "APP_API_KEY" in security["description"]
    body = analyze_body(schema)
    assert body["required"] == ["file"]
    assert body["properties"]["use_llm"]["default"] is False
    assert body["properties"]["allow_external_transfer"]["default"] is False


def test_response_fields_and_nested_metrics_have_help(client):
    schema = client.get("/openapi.json").json()["components"]["schemas"]
    for name in ("AnalysisResponse", "Finding", "KnowledgeItem"):
        assert all(field.get("description") for field in schema[name]["properties"].values())
    report = schema["AnalysisResponse"]["properties"]
    assert "rules_only" in report["mode"]["description"]
    assert "partial" in report["status"]["description"]
    assert "не означает" in report["status"]["description"]
    metrics = report["metrics"]["properties"]
    assert set(metrics) == {"total_findings", "by_category", "by_severity",
                            "paragraph_count", "quality_score", "applied_rules", "document_profile",
                            "structure", "language_check", "llm_validation", "timings_ms", "policy_checks"}
    assert all(field.get("description") for field in metrics.values())
    assert schema["Finding"]["properties"]["verified"]["description"]


def test_analysis_examples_match_real_response_shape(client):
    operation = client.get("/openapi.json").json()["paths"]["/api/v1/analyze"]["post"]
    examples = operation["responses"]["200"]["content"]["application/json"]["examples"]
    assert set(examples) == {"rules_only", "hybrid", "partial"}
    for example in examples.values():
        report = AnalysisResponse.model_validate(example["value"])
        assert report.metrics["total_findings"] == len(report.findings)
        # FastAPI omits None values when serializing OpenAPI examples.
        assert report.metrics.get("quality_score") is None
        assert all(not finding.verified for finding in report.findings if finding.method == "llm")
    assert examples["partial"]["value"]["status"] == "partial"
    assert examples["hybrid"]["value"]["status"] == "completed"
    assert set(operation["responses"]) >= {"200", "401", "409", "410", "413", "415", "422"}
    assert "X-Request-ID" in operation["responses"]["200"]["headers"]


def test_openapi_never_uses_configured_secret_as_example(client, monkeypatch):
    sentinel = "PRIVATE_DOCS_SENTINEL_NOT_AN_EXAMPLE"
    monkeypatch.setattr(get_settings(), "app_api_key", sentinel)
    monkeypatch.setattr(get_settings(), "openclaw_api_key", sentinel)
    response = client.get("/openapi.json")
    assert response.status_code == 200
    assert sentinel not in json.dumps(response.json())


def test_documented_optional_header_rejects_empty_value_without_llm(client, headers, monkeypatch):
    async def forbidden(*args, **kwargs):
        raise AssertionError("A documentation check must not send a document")
    monkeypatch.setattr("app.main.analyze_with_llm", forbidden)
    response = client.post("/api/v1/analyze", headers={**headers, "Idempotency-Key": ""},
                           files={"file": document_file()})
    assert response.status_code == 422
