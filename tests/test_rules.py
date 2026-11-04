from app.document_parser import ParagraphInfo, ParsedDocument
from app.rules import inspect_document


def make(text):
    return ParsedDocument([ParagraphInfo(0, text, ())], text, 1, 0)


def test_vague_deadline_detected():
    findings = inspect_document(make("Просим в кратчайшие сроки представить сведения."))
    assert any(x.code == "CONTENT_VAGUE_DEADLINE" and x.evidence for x in findings)


def test_clean_example_has_no_rule_violations():
    assert not inspect_document(make("Просим направить ответ до 10 ноября 2026 года."))


def test_empty_document_is_critical():
    assert inspect_document(make(""))[0].severity == "critical"
