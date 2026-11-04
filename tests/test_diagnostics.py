from dataclasses import replace

import httpx
import pytest
from docx import Document
from test_letter_checks import evaluate, parsed

from app.diagnostics import local_warnings, policy_text
from app.document_parser import ParagraphInfo, ParsedDocument
from app.rules import inspect_document
from scripts.create_letter_demos import demo_cases


def test_each_demo_has_concrete_values_and_a_specific_action():
    from app.document_parser import parse_docx
    expectations = {
        'ORG_MARGINS': ('20', '27,5', '2,75 см'),
        'ORG_BODY_FONT': ('Arial', 'Times New Roman', 'гарнитуру'),
        'ORG_BODY_FONT_SIZE': ('16', '10–14', '10 до 14'),
        'ORG_BODY_FIRST_LINE': ('0 мм', '12,5', '1,25 см'),
        'ORG_BODY_LINE_SPACING': ('8 пт', '12 пт', 'точно'),
        'ORG_RECIPIENT_FONT': ('10 пт', '14 пт', '14 пт'),
        'ORG_EXECUTOR_CONTACT': ('номер телефона', 'колонтитул', 'самостоятельно'),
        'ORG_EXECUTOR_FONT': ('10 пт', '12 пт', '12 пт'),
        'ORG_DATE_FORMAT': ('24/12/2025', 'разделители', '24.12.2025'),
        'ORG_SIGNATURE_FONT': ('Calibri', 'Times New Roman', 'гарнитуру'),
    }
    for case in demo_cases():
        findings, metrics = evaluate(parse_docx(case['data']))
        finding = findings[0]
        first, second, action = expectations[finding.code]
        assert first in finding.message and second in finding.message, finding.code
        assert action in finding.recommendation, finding.code
        assert 'Приведите проверяемое свойство' not in finding.recommendation
        row = next(r for r in metrics['results'] if r['outcome'] == 'violation')
        assert row['message'] == finding.message
        assert row['recommendation'] == finding.recommendation
        if finding.code == 'ORG_SIGNATURE_FONT':
            assert finding.expected['family'] == 'Times New Roman' and finding.expected['size_pt'] == 12


def test_margin_message_mentions_only_wrong_sides():
    message, action = policy_text('ORG_MARGINS', 'section[0]',
        {'left_mm': 27.5, 'right_mm': 22.5, 'top_mm': 20, 'bottom_mm': 20},
        {'left_mm': 20, 'right_mm': 22.5, 'top_mm': 20, 'bottom_mm': 20}, 'violation')
    assert 'раздел 1' in message.lower()
    assert 'левое поле' in message and 'правое поле' not in message + action


def test_calendar_error_does_not_invent_a_replacement_date():
    message, action = policy_text('ORG_DATE_FORMAT', 'body/p[4]', {}, {'date_text': '31.02.2025'}, 'violation')
    assert 'не существует в календаре' in message
    assert '28.02.2025' not in action
    assert 'фактическую дату' in action


def test_unknown_font_and_missing_executor_are_not_reported_as_violations():
    message, _ = policy_text('ORG_BODY_FONT_SIZE', 'body/p[7]', {'min_pt': 10, 'max_pt': 14},
                           {'family': 'Times New Roman', 'size_pt': None, 'bold': False}, 'insufficient_data')
    assert 'размер' in message and 'не удалось определить' in message
    doc = Document()
    doc.add_paragraph('Произвольный текст')
    findings, metrics = evaluate(parsed(doc))
    assert not any(f.code == 'ORG_EXECUTOR_CONTACT' for f in findings)
    warning = next(w for w in local_warnings(parsed(doc), metrics) if 'ORG_EXECUTOR_CONTACT' in w)
    assert 'Исполнитель:' in warning and 'не означает отсутствие' in warning


def test_images_and_fields_warnings_are_conditional_and_do_not_echo_instructions():
    doc = ParsedDocument([ParagraphInfo(0, 'Текст', (), fragment_id='body/p[0]')], 'Текст', 1, 0)
    warnings = local_warnings(doc, {'profile': None})
    assert len(warnings) == 1 and 'policy_profile=' in warnings[0]
    picture = replace(doc.paragraphs[0], image_count=2)
    warnings = local_warnings(replace(doc, paragraphs=[picture]), {'profile': None})
    assert any('изображения: 2' in w and 'блок 1' in w for w in warnings)
    from app.document_parser import FieldInfo
    field = FieldInfo('field[0]', 'PRIVATE_UNTRUSTED_INSTRUCTION', 'cached data', True)
    warnings = local_warnings(replace(doc, paragraphs=[replace(picture, fields=(field,))]), {'profile': None})
    assert any('полей Word: 1' in w for w in warnings)
    assert 'PRIVATE_UNTRUSTED_INSTRUCTION' not in ' '.join(warnings)


def test_missing_left_indent_is_not_presented_as_a_measured_zero():
    message, _ = policy_text('ORG_BODY_FIRST_LINE', 'body/p[0]', {'first_line_mm': 12.5},
                            {'first_line_mm': 0, 'left_indent_mm': None}, 'violation')
    assert 'левое смещение отдельно не определено' in message
    assert 'начало абзаца — 0 мм' not in message


def test_legacy_long_paragraph_mentions_measured_length_and_threshold():
    doc = ParsedDocument([ParagraphInfo(0, 'Я' * 700, (), fragment_id='body/p[0]')], 'Я' * 700, 1, 0)
    finding = next(f for f in inspect_document(doc) if f.code == 'LANG_LONG_PARAGRAPH')
    assert '700 символов' in finding.message and '650' in finding.message
    assert 'а не нарушение инструкции' in finding.message


@pytest.mark.parametrize('kind', ['timeout', 'connection', 'bad_json'])
def test_language_failures_explain_stage_without_exposing_upstream_data(monkeypatch, kind):
    import asyncio

    from app.config import Settings
    from app.language import LanguageCheckError, check_language

    original = httpx.AsyncClient
    def handler(request):
        if kind == 'timeout':
            raise httpx.ReadTimeout('PRIVATE_KEY_IN_LOW_LEVEL_ERROR', request=request)
        if kind == 'connection':
            raise httpx.ConnectError('PRIVATE_KEY_IN_LOW_LEVEL_ERROR', request=request)
        return httpx.Response(200, text='PRIVATE_KEY_IN_RESPONSE')
    monkeypatch.setattr('app.language.httpx.AsyncClient', lambda **kwargs: original(**kwargs, transport=httpx.MockTransport(handler)))
    settings = Settings(_env_file=None, language_tool_enabled=True, language_tool_timeout_seconds=7)
    doc = ParsedDocument([], 'Текст', 1, 0)
    with pytest.raises(LanguageCheckError) as error:
        asyncio.run(check_language(settings, doc))
    message = str(error.value)
    assert 'PRIVATE_KEY' not in message
    assert {'timeout': '7 с', 'connection': 'соединиться', 'bad_json': 'схеме 6.6'}[kind] in message
