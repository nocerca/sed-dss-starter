from dataclasses import replace
from io import BytesIO

import pytest
from docx.shared import Mm, Pt

from app.document_parser import parse_docx
from app.letter_checks import IMPLEMENTED, PROFILE_ID, evaluate_policy
from app.rule_registry import load_registry
from app.rules import applicable_rules
from app.schemas import DocumentType
from scripts.create_letter_demos import baseline_document, demo_cases


def parsed(document):
    stream = BytesIO()
    document.save(stream)
    return parse_docx(stream.getvalue())


def evaluate(document):
    rules = applicable_rules(load_registry(), DocumentType.request, None, policy_profile=PROFILE_ID)
    return evaluate_policy(document, rules)


def test_baseline_and_both_date_formats_are_allowed():
    doc = baseline_document()
    for date_text in ('24.12.2025 № УЧ-001', '24 декабря 2025 г. № УЧ-001'):
        doc.paragraphs[4].runs[0].text = date_text
        findings, metrics = evaluate(parsed(doc))
        assert findings == []
        assert metrics['counts']['pass'] > 0
        assert {r['code'] for r in metrics['results']} == set(IMPLEMENTED)
        assert next(r for r in metrics['results'] if r['code'] == 'ORG_DATE_FORMAT')['outcome'] == 'pass'


def test_ten_mutations_have_exactly_the_documented_error_codes():
    for case in demo_cases():
        findings, _ = evaluate(parse_docx(case['data']))
        assert {finding.code for finding in findings} == set(case['expected_codes']), case['filename']
        assert all(f.expected and f.observed and f.source_ref and f.fragment_id for f in findings)


def test_api_profile_is_explicit_and_idempotency_respects_it(client, headers, monkeypatch):
    async def forbidden(*args, **kwargs):
        pytest.fail('A local profile must not call a model')

    monkeypatch.setattr('app.main.analyze_with_llm', forbidden)
    case = demo_cases()[0]
    file = (case['filename'], case['data'], 'application/vnd.openxmlformats-officedocument.wordprocessingml.document')
    from uuid import uuid4
    auth = {**headers, 'Idempotency-Key': uuid4().hex}
    default = client.post('/api/v1/analyze', headers=auth, files={'file': file}).json()
    assert not any(f['code'].startswith('ORG_') for f in default['findings'])
    assert default['metrics']['policy_checks']['status'] == 'not_requested'
    assert client.post('/api/v1/analyze', headers=auth, files={'file': file}, data={'policy_profile': PROFILE_ID}).status_code == 409
    response = client.post('/api/v1/analyze', headers=headers, files={'file': file}, data={'policy_profile': PROFILE_ID})
    assert response.status_code == 200
    report = response.json()
    assert report['provider'] == 'none' and report['status'] == 'completed'
    assert any(f['code'] == 'ORG_MARGINS' for f in report['findings'])
    assert report['metrics']['policy_checks']['profile'] == PROFILE_ID
    saved = client.get('/api/v1/reports/' + report['id'], headers=headers).json()
    assert saved['metrics']['policy_checks'] == report['metrics']['policy_checks']
    assert client.post('/api/v1/analyze', headers=headers, files={'file': file}, data={'policy_profile': 'other'}).status_code == 422


def test_unknown_roles_and_unsupported_layout_never_mean_missing_requisites():
    from docx import Document
    document = Document()
    document.add_paragraph('Произвольный текст без реквизитов')
    findings, metrics = evaluate(parsed(document))
    assert not any(f.code == 'ORG_EXECUTOR_CONTACT' for f in findings)
    assert next(r for r in metrics['results'] if r['code'] == 'ORG_EXECUTOR_CONTACT')['outcome'] == 'insufficient_data'
    doc = parsed(baseline_document())
    changed = replace(doc, sections=tuple(replace(section, mirrored_margins=True) for section in doc.sections))
    _, metrics = evaluate(changed)
    assert next(r for r in metrics['results'] if r['code'] == 'ORG_MARGINS')['outcome'] == 'insufficient_data'


def test_executor_can_be_in_body_and_organization_phone_is_not_a_substitute():
    document = baseline_document()
    footer = document.sections[0].footer
    for p in footer.paragraphs:
        p.text = ''
    document.add_paragraph('Исполнитель: Пример Примерович Примеров')
    findings, _ = evaluate(parsed(document))
    assert any(f.code == 'ORG_EXECUTOR_CONTACT' for f in findings)
    document.add_paragraph('Тел.: 000-00-00')
    findings, _ = evaluate(parsed(document))
    assert not any(f.code == 'ORG_EXECUTOR_CONTACT' for f in findings)


def test_inherited_paragraph_properties_and_unknown_spacing_units():
    document = baseline_document()
    style = document.styles.add_style('InheritedBody', 1)
    style.base_style = document.styles['Normal']
    style.paragraph_format.first_line_indent = Mm(12.5)
    style.paragraph_format.line_spacing = Pt(12)
    paragraph = document.paragraphs[7]
    paragraph.style = style
    paragraph.paragraph_format.first_line_indent = None
    paragraph.paragraph_format.line_spacing = None
    doc = parsed(document)
    target = next(p for p in doc.paragraphs if p.fragment_id == 'body/p[7]')
    assert abs(target.first_line_indent_mm - 12.5) < .1
    assert target.line_spacing_mode == 'exact' and target.line_spacing_value == 12
    findings, _ = evaluate(doc)
    assert findings == []
    paragraph.paragraph_format.line_spacing = Pt(15)
    _, metrics = evaluate(parsed(document))
    row = next(r for r in metrics['results'] if r['code'] == 'ORG_BODY_LINE_SPACING' and r['fragment_id'] == 'body/p[7]')
    assert row['outcome'] == 'insufficient_data'


def test_bounded_policy_reports_are_explicitly_partial(client, headers, monkeypatch):
    monkeypatch.setattr('app.letter_checks.MAX_RESULTS', 5)
    case = demo_cases()[0]
    report = client.post('/api/v1/analyze', headers=headers,
        files={'file': (case['filename'], case['data'])}, data={'policy_profile': PROFILE_ID}).json()
    assert report['status'] == 'partial'
    assert report['metrics']['policy_checks']['truncated'] is True
    assert len(report['metrics']['policy_checks']['results']) == 5
    assert any('лимит' in warning for warning in report['warnings'])


@pytest.mark.parametrize('text', ['31.02.2025', '24.12.25', '24.12.2025 г.', '08 января 2025 г.'])
def test_invalid_document_dates(text):
    document = baseline_document()
    document.paragraphs[4].runs[0].text = text + ' № УЧ-001'
    findings, _ = evaluate(parsed(document))
    assert any(f.code == 'ORG_DATE_FORMAT' for f in findings)
