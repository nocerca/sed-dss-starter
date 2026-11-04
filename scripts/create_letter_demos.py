"""Reproducible synthetic letters: ten controlled defects, no private source text."""

import argparse
import json
from io import BytesIO
from pathlib import Path

from docx import Document
from docx.enum.text import WD_LINE_SPACING
from docx.shared import Mm, Pt

ROOT = Path(__file__).resolve().parent.parent


def baseline_document():
    document = Document()
    document.core_properties.author = "SED-DSS synthetic generator"
    document.core_properties.last_modified_by = "SED-DSS synthetic generator"
    document.core_properties.title = "Синтетическое исходящее письмо"
    section = document.sections[0]
    section.left_margin, section.right_margin = Mm(27.5), Mm(22.5)
    section.top_margin = section.bottom_margin = Mm(20)
    normal = document.styles["Normal"]
    normal.font.name, normal.font.size, normal.font.bold = "Times New Roman", Pt(12), False
    normal.paragraph_format.first_line_indent = Mm(0)
    normal.paragraph_format.left_indent = Mm(0)
    normal.paragraph_format.line_spacing_rule = WD_LINE_SPACING.EXACTLY
    normal.paragraph_format.line_spacing = Pt(12)

    def paragraph(text, size=12, indent=0, container=document):
        p = container.add_paragraph()
        p.paragraph_format.first_line_indent = Mm(indent)
        run = p.add_run(text)
        run.font.name, run.font.size, run.font.bold = "Times New Roman", Pt(size), False
        return p

    paragraph('Учебная организация «Синтетический отправитель»')
    paragraph('ИНН: учебный; КПП: учебный; office@example.invalid; тел. 000-00-00', size=9)
    paragraph('Директору учебной организации', size=14)
    paragraph('И.О.Получателю', size=14)
    paragraph('24.12.2025 № УЧ-001')
    paragraph('О предоставлении учебных материалов')
    paragraph('Уважаемый адресат!')
    paragraph('Просим направить перечень учебных материалов для подготовки учебного мероприятия.', indent=12.5)
    paragraph('Сообщаем, что это искусственный документ для проверки сервиса.', indent=12.5)
    paragraph('Приложение: перечень учебных материалов на 1 л. в 1 экз.')
    paragraph('С уважением,')
    paragraph('Директор учебной организации    И.О.Примеров')
    footer = section.footer
    footer.paragraphs[0].text = ''
    paragraph('Исполнитель: Пример Примерович Примеров', container=footer)
    paragraph('Тел.: 000-00-00', container=footer)
    return document


def _bytes(document) -> bytes:
    stream = BytesIO()
    document.save(stream)
    return stream.getvalue()


def demo_cases() -> list[dict]:
    cases = []
    definitions = [
        ('01_left_margin', 'Левое поле 20 мм вместо 27,5 мм', 'ORG_MARGINS', 'section[0]', 27.5, 20),
        ('02_body_font', 'Arial в основном тексте', 'ORG_BODY_FONT', 'body/p[7]/r[0]', 'Times New Roman', 'Arial'),
        ('03_body_size', 'Основной текст 16 пт', 'ORG_BODY_FONT_SIZE', 'body/p[7]/r[0]', 12, 16),
        ('04_body_indent', 'Абзацный отступ 0 мм', 'ORG_BODY_FIRST_LINE', 'body/p[7]', 12.5, 0),
        ('05_body_spacing', 'Межстрочный интервал 8 пт точно', 'ORG_BODY_LINE_SPACING', 'body/p[7]', 12, 8),
        ('06_recipient_size', 'Адресат 10 пт вместо 14 пт', 'ORG_RECIPIENT_FONT', 'body/p[2]/r[0]', 14, 10),
        ('07_executor_phone', 'Удалён телефон исполнителя', 'ORG_EXECUTOR_CONTACT', 'section[0]/footer/p[1]', True, False),
        ('08_executor_size', 'Исполнитель 10 пт вместо 12 пт', 'ORG_EXECUTOR_FONT', 'section[0]/footer/p[1]/r[0]', 12, 10),
        ('09_date_format', 'Дата со слешами', 'ORG_DATE_FORMAT', 'body/p[4]', '24.12.2025', '24/12/2025'),
        ('10_signature_font', 'Подпись Calibri вместо шрифта текста', 'ORG_SIGNATURE_FONT', 'body/p[11]/r[0]', 'Times New Roman', 'Calibri'),
    ]
    for index, (name, description, code, selector, before, after) in enumerate(definitions):
        document = baseline_document()
        paragraphs = document.paragraphs
        if index == 0:
            document.sections[0].left_margin = Mm(20)
        elif index == 1:
            paragraphs[7].runs[0].font.name = 'Arial'
        elif index == 2:
            paragraphs[7].runs[0].font.size = Pt(16)
        elif index == 3:
            paragraphs[7].paragraph_format.first_line_indent = Mm(0)
        elif index == 4:
            paragraphs[7].paragraph_format.line_spacing = Pt(8)
        elif index == 5:
            paragraphs[2].runs[0].font.size = Pt(10)
        elif index == 6:
            document.sections[0].footer.paragraphs[-1].text = ''
        elif index == 7:
            document.sections[0].footer.paragraphs[1].runs[0].font.size = Pt(10)
        elif index == 8:
            paragraphs[4].runs[0].text = '24/12/2025 № УЧ-001'
        else:
            paragraphs[11].runs[0].font.name = 'Calibri'
        cases.append({'filename': name + '.docx', 'description': description,
                      'expected_codes': [code], 'selector': selector, 'before': before,
                      'after': after, 'data': _bytes(document)})
    return cases


def export_demos(target: Path) -> None:
    """Only write synthetic material into a dedicated output folder."""
    bad = target / 'incorrect'
    controls = target / 'controls'
    bad.mkdir(parents=True, exist_ok=True)
    controls.mkdir(parents=True, exist_ok=True)
    manifest = []
    for case in demo_cases():
        (bad / case['filename']).write_bytes(case['data'])
        manifest.append({key: value for key, value in case.items() if key != 'data'})
    (controls / 'baseline.docx').write_bytes(_bytes(baseline_document()))
    verbal = baseline_document()
    verbal.paragraphs[4].runs[0].text = '24 декабря 2025 г. № УЧ-001'
    (controls / 'allowed_verbal_date.docx').write_bytes(_bytes(verbal))
    (target / 'manifest.json').write_text(json.dumps({
        'synthetic': True, 'source_letters_used': False,
        'policy_profile': 'office_instruction_letters', 'use_llm': False,
        'allow_external_transfer': False, 'cases': manifest,
        'controls': ['baseline.docx', 'allowed_verbal_date.docx'],
        'limitations': 'Контроль только реализованных свойств. Не эталон полного соответствия инструкции.',
    }, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    (target / 'README.md').write_text(
        '# Проверка десяти документов в Postman\n\n'
        'Все документы синтетические. Оригиналы писем не использовались.\n\n'
        'Импортируйте postman/SED-DSS.postman_collection.json и окружение. '
        'Укажите app_api_key из APP_API_KEY локального .env. '
        'В папке «Проверки по инструкции» есть десять готовых запросов. '
        'Рабочая папка Postman — корень проекта; если путь не найден, выберите файл вручную. '
        'Поля: policy_profile=office_instruction_letters, use_llm=false, '
        'allow_external_transfer=false. При каждом Send создаётся новый Idempotency-Key.\n\n'
        '| Файл | Внесённая ошибка | Ожидаемый код |\n|---|---|---|\n' +
        '\n'.join(f"| {case['filename']} | {case['description']} | {case['expected_codes'][0]} |" for case in manifest) +
        '\n\nОжидаемый код ищите в findings. В expected/observed — значения, '
        'в source_ref — основание, в metrics.policy_checks — исходы проверок. '
        'Папка controls содержит допустимые контрольные варианты: '
        'для реализованных проверок нарушения не ожидаются.\n', encoding='utf-8')


if __name__ == '__main__':
    argparse.ArgumentParser(description='Создать десять искусственных DOCX с контролируемыми дефектами').parse_args()
    export_demos(ROOT / 'examples' / 'private' / 'postman_cases')
    print('Созданы 10 некорректных DOCX, 2 контроля и manifest; все данные синтетические.')
