"""Typed applicability and immutable rule specifications; samples are not standards."""
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.orm import Session

from app.models import RuleDefinition, RuleVersion
from app.schemas import DocumentType

RULES_PATH = Path(__file__).resolve().parent.parent / "config" / "rules.json"


class Applicability(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_types: list[DocumentType] = Field(min_length=1)
    templates: list[str] = Field(default_factory=list)
    policy_profiles: list[str] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=lambda: [
        "body", "header", "footer", "first_page_header", "first_page_footer",
        "even_page_header", "even_page_footer",
    ], min_length=1)
    explanation: str = Field(min_length=10, max_length=1000)

    def applies(self, document_type: DocumentType, template_id: str | None,
                policy_profile: str | None = None) -> bool:
        return (document_type in self.document_types
                and (not self.templates or template_id in self.templates)
                and (not self.policy_profiles or policy_profile in self.policy_profiles))


class Source(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["heuristic", "normative", "instruction"]
    ref: str = Field(min_length=1, max_length=255)
    title: str = Field(min_length=3, max_length=500)
    uri: str | None = None
    verified: bool = False


class RuleSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(pattern="^[A-Z][A-Z0-9_]{0,59}$")
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    category: Literal["content", "language", "formatting"]
    severity: Literal["info", "warning", "critical"]
    description: str = Field(min_length=3, max_length=1000)
    recommendation: str = Field(min_length=3, max_length=1000)
    implementation: Literal["phrase", "empty_text", "long_paragraph", "many_fonts", "letter_policy"]
    policy_specification: dict | None = None
    patterns: list[str] = Field(default_factory=list)
    threshold: int | None = Field(default=None, ge=1)
    source: Source
    applicability: Applicability

    @model_validator(mode="after")
    def check_specification(self):
        if self.implementation == "phrase" and (not self.patterns or any(not p.strip() for p in self.patterns)):
            raise ValueError("Phrase rules require nonempty patterns")
        if self.implementation in {"long_paragraph", "many_fonts"} and self.threshold is None:
            raise ValueError("The implementation requires a threshold")
        if self.source.kind == "normative" and (not self.source.verified or not self.source.uri):
            raise ValueError("A normative rule requires a verified source and an original-source reference")
        if self.source.kind == "heuristic" and self.source.verified:
            raise ValueError("Heuristics must not be labelled verified normative sources")
        if self.implementation == "letter_policy" and (
            not self.policy_specification or not self.applicability.policy_profiles
            or self.source.kind != "instruction" or not self.source.uri or self.source.verified
        ):
            raise ValueError("Instruction checks need an explicit profile and an auditable specification")
        return self


class RuleSet(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: str = Field(min_length=1, max_length=40)
    rules: list[RuleSpec] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_codes(self):
        if len({rule.code for rule in self.rules}) != len(self.rules):
            raise ValueError("Rule codes must be unique in a ruleset")
        return self


def load_registry(path: Path = RULES_PATH) -> RuleSet:
    try:
        return RuleSet.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        raise ValueError("Не удалось загрузить формальную спецификацию правил") from None


def register_rules(db: Session, registry: RuleSet) -> None:
    for rule in registry.rules:
        specification = rule.model_dump(mode="json")
        previous = db.get(RuleVersion, (rule.code, rule.version))
        if previous and previous.specification != specification:
            raise ValueError("Спецификация правила изменилась без увеличения версии")
        if not previous:
            db.add(RuleVersion(code=rule.code, version=rule.version, specification=specification))
        definition = db.get(RuleDefinition, rule.code)
        if definition is None:
            db.add(RuleDefinition(
                code=rule.code, category=rule.category, severity=rule.severity,
                description=rule.description, enabled=1,
            ))
        else:
            definition.category, definition.severity = rule.category, rule.severity
            definition.description = rule.description
    db.commit()
