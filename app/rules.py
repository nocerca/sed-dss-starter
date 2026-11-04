"""Deterministic implementations of explicitly versioned prototype rules."""
import re

from app.document_parser import ParsedDocument
from app.rule_registry import RuleSet, RuleSpec, load_registry
from app.schemas import DocumentType, Finding


def applicable_rules(
    registry: RuleSet, document_type: DocumentType, template_id: str | None,
    enabled_codes: set[str] | None = None,
    policy_profile: str | None = None,
) -> list[RuleSpec]:
    return [rule for rule in registry.rules
            if rule.applicability.applies(document_type, template_id, policy_profile)
            and (enabled_codes is None or rule.code in enabled_codes)]


def inspect_document(
    doc: ParsedDocument, document_type: DocumentType = DocumentType.unspecified,
    template_id: str | None = None, *, registry: RuleSet | None = None,
    enabled_codes: set[str] | None = None,
) -> list[Finding]:
    findings: list[Finding] = []
    for rule in applicable_rules(registry or load_registry(), document_type, template_id, enabled_codes):
        def finding(evidence: str = "", fragment_id: str | None = None) -> Finding:
            return Finding(
                code=rule.code, category=rule.category, severity=rule.severity,
                message=rule.description, recommendation=rule.recommendation,
                evidence=evidence[:600], source_ref=rule.source.ref,
                fragment_id=fragment_id, rule_version=rule.version,
            )

        paragraphs = [p for p in doc.paragraphs if p.location in rule.applicability.locations]
        if rule.implementation == "empty_text":
            if not doc.text.strip():
                findings.append(finding())
        elif rule.implementation == "phrase":
            for paragraph in paragraphs:
                match = next((match for pattern in rule.patterns
                              if (match := re.search(re.escape(pattern), paragraph.text, re.IGNORECASE))), None)
                if match:
                    findings.append(finding(match.group(0), paragraph.fragment_id or None))
                    break
        elif rule.implementation == "long_paragraph":
            for paragraph in paragraphs:
                if len(paragraph.text.strip()) > rule.threshold:
                    findings.append(finding(paragraph.text[:130], paragraph.fragment_id or None))
        elif rule.implementation == "many_fonts":
            fonts = {font for paragraph in paragraphs for font in paragraph.font_names}
            if len(fonts) >= rule.threshold:
                findings.append(finding(", ".join(sorted(fonts))))
    return findings
