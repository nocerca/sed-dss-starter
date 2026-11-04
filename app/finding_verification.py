"""Conservative admission gates. Exact quotes are necessary but not proof of a claim."""

import re
from collections import Counter

from app.document_context import fragment_for_evidence, heading_like
from app.document_parser import ParsedDocument
from app.retrieval import Context
from app.schemas import Finding, LLMFindings


class FindingsResult(list[Finding]):
    def __init__(self, findings: list[Finding], received: int, rejected: dict[str, int]):
        super().__init__(findings)
        self.received = received
        self.rejected = rejected


ABSENCE = re.compile(
    r"отсутств\w*|\bнет\s+(?:описан\w*|код\w*|скриншот\w*|подпис\w*|реквизит\w*)|"
    r"не\s+(?:указан\w*|привед\w*|представлен\w*|описан\w*|раскрыт\w*|содержит)|"
    r"без\s+(?:описан\w*|код\w*|скриншот\w*|изображен\w*)|не\s+хвата\w*|"
    r"не\s+(?:присутств\w*|обнаруж\w*)|\bmissing\b|not\s+(?:provided|present)", re.IGNORECASE,
)
NORMATIVE = re.compile(r"\bГОСТ\b|обязательн\w*\s+(?:реквизит\w*|подпис\w*|печать)|наруш\w*\s+норм\w*", re.IGNORECASE)


def admit_findings(payload: dict, text: str, contexts: list[Context],
                   document: ParsedDocument | None = None) -> FindingsResult:
    candidates = LLMFindings.model_validate(payload)
    normative_refs = {ctx.source_ref for ctx in contexts if ctx.kind == "normative" and ctx.source_ref}
    accepted = []
    rejected: Counter = Counter()
    seen = set()
    for candidate in candidates.findings:
        claim = candidate.message + " " + candidate.recommendation
        reason = None
        if not candidate.evidence.strip() or candidate.evidence not in text:
            reason = "quote_not_found"
        elif candidate.claim_type != "text_issue" or ABSENCE.search(claim):
            reason = "unobservable_claim"
        elif candidate.category == "formatting":
            reason = "visual_claim"
        elif NORMATIVE.search(claim):
            reason = "unverified_normative_claim"
        elif heading_like(candidate.evidence) or (document and any(
            p.heading_level and p.text.strip() == candidate.evidence.strip() for p in document.paragraphs
        )):
            reason = "heading_only"
        key = (candidate.category, candidate.evidence.casefold().strip())
        if not reason and key in seen:
            reason = "duplicate"
        if not reason and len(accepted) >= 5:
            reason = "limit"
        if reason:
            rejected[reason] += 1
            continue
        seen.add(key)
        accepted.append(Finding(
            code=candidate.code, category=candidate.category, severity=candidate.severity,
            message=candidate.message, evidence=candidate.evidence,
            recommendation=candidate.recommendation,
            source_ref=candidate.source_ref if candidate.source_ref in normative_refs else None,
            method="llm", verified=False,
            fragment_id=fragment_for_evidence(document, candidate.evidence) if document else None,
        ))
    return FindingsResult(accepted, len(candidates.findings), dict(rejected))
