import pytest
from test_document_context import parsed
from test_providers import candidate

from app.providers import verify_findings


@pytest.mark.parametrize("change,reason", [
    ({"message": "В документе отсутствует скриншот"}, "unobservable_claim"),
    ({"recommendation": "Описание не представлено, добавьте его"}, "unobservable_claim"),
    ({"claim_type": "missing_content"}, "unobservable_claim"),
    ({"claim_type": "visual_issue"}, "unobservable_claim"),
    ({"category": "formatting"}, "visual_claim"),
    ({"message": "Это нарушение ГОСТ"}, "unverified_normative_claim"),
    ({"evidence": "выдуманная цитата"}, "quote_not_found"),
])
def test_unobservable_or_unsupported_claims_are_discarded(change, reason):
    result = verify_findings({"findings": [candidate(**change)]}, "Просим в кратчайшие сроки", [])
    assert result == []
    assert result.received == 1 and result.rejected == {reason: 1}


def test_heading_only_is_not_proof_of_missing_content():
    d = parsed("Вывод:", "Описание результата уже есть.")
    result = verify_findings({"findings": [candidate(evidence="Вывод:", message="Неопределённый термин") ]}, d.text, [], d)
    assert result == [] and result.rejected == {"heading_only": 1}


def test_literal_issue_has_structural_address_and_duplicate_codes_do_not_bypass_filter():
    d = parsed("Просим в кратчайшие сроки сообщить ответ.")
    result = verify_findings({"findings": [candidate(), candidate(code="ANOTHER_CODE")]}, d.text, [], d)
    assert len(result) == 1 and result.rejected == {"duplicate": 1}
    assert result[0].fragment_id == "body/p[0]" and result[0].verified is False
