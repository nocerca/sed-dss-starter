import base64
import json
from io import BytesIO

from docx import Document

from app.document_context import document_profile, model_structure
from app.document_parser import parse_docx

PIXEL = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/l1sAAAAASUVORK5CYII=")


def parsed(*lines):
    d = Document()
    for line in lines:
        d.add_paragraph(line)
    stream = BytesIO()
    d.save(stream)
    return parse_docx(stream.getvalue())


def test_images_and_heading_are_counted_without_transmitting_content():
    d = Document()
    d.add_paragraph("Результат работы:", style="Heading 1")
    d.add_picture(BytesIO(PIXEL))
    d.add_paragraph("Вывод:")
    d.add_paragraph("Результат описан обычным текстом.")
    stream = BytesIO()
    d.save(stream)
    document = parse_docx(stream.getvalue())
    assert document.paragraphs[0].heading_level == 1
    assert sum(p.image_count for p in document.paragraphs) == 1
    structure = model_structure(document)
    assert structure["image_references"] == 1
    assert structure["images_content_available"] is False
    assert structure["complete"] is True
    assert any(b.get("heading") == "Результат работы:" for b in structure["blocks"])
    assert any(b["images"] == 1 for b in structure["blocks"])
    assert "base64" not in json.dumps(structure) and "rId" not in json.dumps(structure)


def test_structural_headings_do_not_leak_text_after_excerpt():
    d = parsed("а" * 12000, "PRIVATE_TRAILING_HEADING:")
    structure = model_structure(d)
    assert "PRIVATE_TRAILING_HEADING" not in json.dumps(structure)
    assert structure["complete"] is False
    assert all(b["end"] <= 12000 for b in structure["blocks"])


def test_profile_is_conservative_and_does_not_invent_a_confidence():
    assert document_profile(parsed("Просим сообщить ответ"))["classification"] == "undetermined"
    assert document_profile(parsed("Уважаемый коллега!", "Направляем письмо."))["classification"] == "likely_letter"
    report = document_profile(parsed("Лабораторная работа", "Цель работы", "Ход работы", "Вывод"))
    assert report["classification"] == "likely_non_letter"
    assert "confidence" not in report
    # A single mention of a lab is insufficient to reject an actual letter.
    assert document_profile(parsed("Просим прислать лабораторную работу"))["classification"] == "undetermined"
