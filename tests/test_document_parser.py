from io import BytesIO

import pytest
from docx import Document

from app.document_parser import DocumentParseError, parse_docx


def make_docx(*lines):
    d = Document()
    for line in lines:
        d.add_paragraph(line)
    buf = BytesIO()
    d.save(buf)
    return buf.getvalue()


def test_parsing_docx():
    d = parse_docx(make_docx("Письмо", "Просим в кратчайшие сроки сообщить ответ"))
    assert d.paragraph_count == 2
    assert "Просим" in d.text


def test_reject_corrupt_archive():
    with pytest.raises(DocumentParseError):
        parse_docx(b"not-a-zip")


def test_block_order_nested_tables_merged_cells_and_headers():
    document = Document()
    document.add_paragraph("Начало")
    table = document.add_table(rows=1, cols=2)
    cell = table.cell(0, 0).merge(table.cell(0, 1))
    cell.text = "Объединённая ячейка"
    cell.add_table(rows=1, cols=1).cell(0, 0).text = "Вложенная таблица"
    document.add_paragraph("Конец")
    document.sections[0].header.paragraphs[0].text = "Заголовок письма"
    document.sections[0].footer.paragraphs[0].text = "Контакт исполнителя"
    stream = BytesIO()
    document.save(stream)
    parsed = parse_docx(stream.getvalue())
    assert parsed.text.index("Начало") < parsed.text.index("Объединённая") < parsed.text.index("Конец")
    assert parsed.text.count("Объединённая ячейка") == 1
    assert "Вложенная таблица" in parsed.text
    assert "Заголовок письма" in parsed.text and "Контакт исполнителя" in parsed.text
    assert len(parsed.tables) == 2 and parsed.tables_count == 1
    ids = [paragraph.fragment_id for paragraph in parsed.paragraphs]
    assert len(ids) == len(set(ids))
    assert ids == [paragraph.fragment_id for paragraph in parse_docx(stream.getvalue()).paragraphs]
    assert any(paragraph.location == "header" for paragraph in parsed.paragraphs)


def test_explicit_run_formatting_and_section_dimensions():
    from docx.shared import Mm, Pt
    document = Document()
    run = document.add_paragraph().add_run("Текст")
    run.font.name, run.font.size, run.bold = "Arial", Pt(12), True
    document.sections[0].left_margin = Mm(25)
    stream = BytesIO()
    document.save(stream)
    parsed = parse_docx(stream.getvalue())
    details = parsed.paragraphs[0].runs[0]
    assert details.font_name == "Arial" and details.font_size_pt == 12
    assert details.bold is True and details.italic is None
    assert parsed.sections[0].left_margin_mm == pytest.approx(25, abs=0.1)
    assert parsed.limitations


def test_linked_headers_not_duplicated():
    document = Document()
    document.sections[0].header.paragraphs[0].text = "Общий колонтитул"
    document.add_section()
    stream = BytesIO()
    document.save(stream)
    assert parse_docx(stream.getvalue()).text.count("Общий колонтитул") == 1


def test_no_header_parts_created_in_source():
    from zipfile import ZipFile
    data = make_docx("Исходное письмо")
    before = data[:]
    parsed = parse_docx(data)
    assert data == before
    assert all(paragraph.location == "body" for paragraph in parsed.paragraphs)
    with ZipFile(BytesIO(data)) as archive:
        assert not any("header" in name for name in archive.namelist())


def test_field_instructions_are_data_and_cached_text_is_not_recomputed():
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    document = Document()
    paragraph = document.add_paragraph("Кэш: ")
    simple = OxmlElement("w:fldSimple")
    simple.set(qn("w:instr"), 'DDEAUTO "never-execute"')
    run = OxmlElement("w:r")
    text = OxmlElement("w:t")
    text.text = "Сохранённый результат"
    run.append(text)
    simple.append(run)
    paragraph._p.append(simple)
    paragraph = document.add_paragraph()
    for kind in ("begin", "separate", "end"):
        run = paragraph.add_run()
        marker = OxmlElement("w:fldChar")
        marker.set(qn("w:fldCharType"), kind)
        run._r.append(marker)
        if kind == "begin":
            code = OxmlElement("w:instrText")
            code.text = " DATE "
            run._r.append(code)
        if kind == "separate":
            paragraph.add_run("01.01.2000")
    stream = BytesIO()
    document.save(stream)
    parsed = parse_docx(stream.getvalue())
    fields = [field for paragraph in parsed.paragraphs for field in paragraph.fields]
    assert fields[0].instructions == 'DDEAUTO "never-execute"'
    assert fields[0].cached_result == "Сохранённый результат"
    assert fields[1].instructions.strip() == "DATE"
    assert fields[1].cached_result == "01.01.2000"
    assert all(field.complete for field in fields)
    assert "never-execute" not in parsed.text
    assert "Сохранённый результат" in parsed.text


def test_legacy_smart_tags_and_content_controls_keep_text_and_formatting():
    from docx.oxml import OxmlElement
    document = Document()
    paragraph = document.add_paragraph("Отступ: ")
    smart = OxmlElement("w:smartTag")
    run, text = OxmlElement("w:r"), OxmlElement("w:t")
    text.text = "1,25 см"
    run.append(text)
    smart.append(run)
    paragraph._p.append(smart)
    control, content = OxmlElement("w:sdt"), OxmlElement("w:sdtContent")
    run, text = OxmlElement("w:r"), OxmlElement("w:t")
    text.text = "; 08.01.2025"
    run.append(text)
    content.append(run)
    control.append(content)
    paragraph._p.append(control)
    stream = BytesIO()
    document.save(stream)
    before = stream.getvalue()
    parsed = parse_docx(before)
    assert parsed.text == "Отступ: 1,25 см; 08.01.2025"
    assert "1,25 см" in [run.text for run in parsed.paragraphs[0].runs]
    assert stream.getvalue() == before


def test_inline_containers_skip_revisions_and_never_execute_field_instructions():
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    document = Document()
    paragraph = document.add_paragraph("Visible ")
    control = OxmlElement("w:sdt")
    content = OxmlElement("w:sdtContent")
    for tag in ("w:ins", "w:del", "w:drawing"):
        hidden = OxmlElement(tag)
        run = OxmlElement("w:r")
        text = OxmlElement("w:t")
        text.text = "REVISION_OR_DRAWING_NOT_INTERPRETED"
        run.append(text)
        hidden.append(run)
        content.append(hidden)
    field = OxmlElement("w:fldSimple")
    field.set(qn("w:instr"), "DDEAUTO external-command-marker")
    run = OxmlElement("w:r")
    text = OxmlElement("w:t")
    text.text = "cached data"
    run.append(text)
    field.append(run)
    content.append(field)
    control.append(content)
    paragraph._p.append(control)
    stream = BytesIO()
    document.save(stream)
    parsed = parse_docx(stream.getvalue())
    assert parsed.text == "Visible cached data"
    assert "external-command-marker" not in parsed.text


def test_excessive_inline_nesting_is_rejected():
    from docx.oxml import OxmlElement

    document = Document()
    current = document.add_paragraph()._p
    for _ in range(66):
        nested = OxmlElement("w:smartTag")
        current.append(nested)
        current = nested
    stream = BytesIO()
    document.save(stream)
    with pytest.raises(DocumentParseError):
        parse_docx(stream.getvalue())


def test_hyperlink_runs_and_inherited_style_without_network(monkeypatch):
    import socket

    from docx.enum.style import WD_STYLE_TYPE
    from docx.opc.constants import RELATIONSHIP_TYPE
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Pt
    document = Document()
    style = document.styles.add_style("Synthetic inherited style", WD_STYLE_TYPE.PARAGRAPH)
    style.font.name, style.font.size, style.font.bold = "Arial", Pt(13), True
    paragraph = document.add_paragraph("Начало ", style=style)
    link = OxmlElement("w:hyperlink")
    link.set(qn("r:id"), paragraph.part.relate_to("https://example.invalid/untrusted", RELATIONSHIP_TYPE.HYPERLINK, is_external=True))
    run, text = OxmlElement("w:r"), OxmlElement("w:t")
    text.text = "ссылка"
    run.append(text)
    link.append(run)
    paragraph._p.append(link)
    stream = BytesIO()
    document.save(stream)
    def forbidden(*args, **kwargs):
        pytest.fail("Document parsing must not open hyperlinks")
    monkeypatch.setattr(socket, "create_connection", forbidden)
    parsed = parse_docx(stream.getvalue())
    runs = parsed.paragraphs[0].runs
    assert runs[0].font_name is None
    assert runs[0].effective_font_name == "Arial" and runs[0].effective_font_size_pt == 13
    assert runs[0].effective_bold is True
    assert runs[1].hyperlink_target == "https://example.invalid/untrusted"
    assert runs[1].text == "ссылка"


def test_section_addresses_are_structural_not_page_guesses():
    document = Document()
    document.add_paragraph("Первый раздел")
    document.add_section()
    document.add_paragraph("Второй раздел")
    stream = BytesIO()
    document.save(stream)
    parsed = parse_docx(stream.getvalue())
    assert next(p for p in parsed.paragraphs if p.text == "Первый раздел").section_index == 0
    assert next(p for p in parsed.paragraphs if p.text == "Второй раздел").section_index == 1
