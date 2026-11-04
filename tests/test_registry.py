import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.db import SessionLocal
from app.document_parser import ParagraphInfo, ParsedDocument
from app.models import RuleVersion
from app.rule_registry import RuleSet, load_registry, register_rules
from app.rules import inspect_document
from app.schemas import DocumentType


def test_rule_applicability_and_version():
    registry = load_registry().model_copy(deep=True)
    rule = next(r for r in registry.rules if r.code == "CONTENT_VAGUE_DEADLINE")
    registry.rules = [rule]
    rule.applicability.document_types = [DocumentType.request]
    rule.applicability.templates = ["synthetic-template"]
    text = "Просим в кратчайшие сроки направить ответ"
    doc = ParsedDocument([ParagraphInfo(0, text, (), fragment_id="body/p[0]")], text, 1, 0)
    assert inspect_document(doc, registry=registry) == []
    assert inspect_document(doc, DocumentType.request, "other", registry=registry) == []
    result = inspect_document(doc, DocumentType.request, "synthetic-template", registry=registry)
    assert len(result) == 1
    assert result[0].rule_version == "1.0.1"
    assert result[0].fragment_id == "body/p[0]"
    assert inspect_document(doc, DocumentType.request, "synthetic-template", registry=registry, enabled_codes=set()) == []


def test_invalid_spec_and_unverified_normative_sources():
    payload = load_registry().model_dump(mode="json")
    payload["rules"][0]["source"]["kind"] = "normative"
    with pytest.raises(ValidationError):
        RuleSet.model_validate(payload)
    payload = load_registry().model_dump(mode="json")
    payload["rules"].append(payload["rules"][0])
    with pytest.raises(ValidationError):
        RuleSet.model_validate(payload)


def test_registry_history_is_immutable(client):
    registry = load_registry().model_copy(deep=True)
    with SessionLocal() as db:
        register_rules(db, registry)
        registry.rules[0].description = "Изменённая формулировка без новой версии"
        with pytest.raises(ValueError, match="без увеличения версии"):
            register_rules(db, registry)
        db.rollback()
        registry.rules[0].version = "1.0.2"
        register_rules(db, registry)
        assert len(db.scalars(select(RuleVersion).where(RuleVersion.code == registry.rules[0].code)).all()) == 2


def test_rules_endpoint_documents_prototype_status(client, headers):
    response = client.get("/api/v1/rules", headers=headers)
    assert response.status_code == 200
    assert len(response.json()) == 21
    for rule in response.json():
        assert rule["source"]["kind"] in {"heuristic", "instruction"}
        assert rule["source"]["verified"] is False
        assert rule["applicability"]["document_types"]
        if rule["source"]["kind"] == "instruction":
            assert rule["applicability"]["policy_profiles"] == ["office_instruction_letters"]
            assert rule["policy_specification"]["source_anchors"]
    code = response.json()[0]["code"]
    assert client.get(f"/api/v1/rules/{code}/versions", headers=headers).status_code == 200
