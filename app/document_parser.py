from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Iterator
from zipfile import BadZipFile, ZipFile

from docx import Document
from docx.enum.text import WD_LINE_SPACING
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.hyperlink import Hyperlink
from docx.text.paragraph import Paragraph
from docx.text.run import Run


@dataclass(frozen=True)
class FieldInfo:
    fragment_id: str
    instructions: str
    cached_result: str
    complete: bool


@dataclass(frozen=True)
class RunInfo:
    fragment_id: str
    text: str
    font_name: str | None
    font_size_pt: float | None
    bold: bool | None
    italic: bool | None
    underline: bool | None
    hyperlink_target: str | None = None
    effective_font_name: str | None = None
    effective_font_size_pt: float | None = None
    effective_bold: bool | None = None
    effective_italic: bool | None = None


@dataclass(frozen=True)
class SectionInfo:
    index: int
    page_width_mm: float | None
    page_height_mm: float | None
    top_margin_mm: float | None
    bottom_margin_mm: float | None
    left_margin_mm: float | None
    right_margin_mm: float | None
    gutter_mm: float | None = None
    mirrored_margins: bool = False


@dataclass(frozen=True)
class TableInfo:
    fragment_id: str
    row_count: int
    column_count: int


class DocumentParseError(ValueError):
    pass


@dataclass(frozen=True)
class ParagraphInfo:
    index: int
    text: str
    font_names: tuple[str, ...]
    fragment_id: str = ""
    location: str = "body"
    style: str | None = None
    alignment: str | None = None
    runs: tuple[RunInfo, ...] = ()
    fields: tuple[FieldInfo, ...] = ()
    section_index: int | None = None
    heading_level: int | None = None
    image_count: int = 0
    first_line_indent_mm: float | None = None
    left_indent_mm: float | None = None
    line_spacing_value: float | None = None
    line_spacing_mode: str | None = None
    numbered: bool = False


@dataclass(frozen=True)
class ParsedDocument:
    paragraphs: list[ParagraphInfo]
    text: str
    paragraph_count: int
    tables_count: int
    sections: tuple[SectionInfo, ...] = ()
    tables: tuple[TableInfo, ...] = ()
    limitations: tuple[str, ...] = field(default_factory=lambda: (
        "Номера страниц и визуальные координаты не вычисляются без рендеринга DOCX",
        "Коды и кэш полей Word читаются как данные и не выполняются; текстовые поля, изображения и исправления не интерпретируются",
        "Наследование шрифта учитывает стили runs и абзацев; темы Word и полное визуальное форматирование не вычисляются",
    ))


def _check_zip(data: bytes) -> None:
    try:
        with ZipFile(BytesIO(data)) as zf:
            infos = zf.infolist()
            if len(infos) > 2000 or sum(x.file_size for x in infos) > 35 * 1024 * 1024:
                raise DocumentParseError("Содержимое DOCX превышает допустимые ограничения")
            if "word/document.xml" not in {x.filename for x in infos}:
                raise DocumentParseError("В архиве нет структуры DOCX")
            if len({x.filename for x in infos}) != len(infos):
                raise DocumentParseError("DOCX содержит повторяющиеся элементы архива")
            if any(x.flag_bits & 1 for x in infos):
                raise DocumentParseError("Зашифрованные DOCX не поддерживаются")
    except (BadZipFile, OSError) as exc:
        raise DocumentParseError("Некорректный формат DOCX") from exc


def _read_fields(paragraph: Paragraph, fragment_id: str) -> tuple[FieldInfo, ...]:
    fields: list[FieldInfo] = []
    stack: list[dict] = []
    next_index = 0
    for node in paragraph._p.iter():
        if node.tag == qn("w:fldSimple"):
            fields.append(FieldInfo(
                f"{fragment_id}/field[{next_index}]", node.get(qn("w:instr"), ""),
                "".join(value.text or "" for value in node.iter(qn("w:t"))), True,
            ))
            next_index += 1
        elif node.tag == qn("w:fldChar"):
            kind = node.get(qn("w:fldCharType"))
            if kind == "begin":
                if len(stack) >= 128:
                    raise DocumentParseError("Слишком глубокая вложенность полей Word")
                stack.append({"id": f"{fragment_id}/field[{next_index}]", "code": [], "result": [], "separated": False})
                next_index += 1
            elif kind == "separate" and stack:
                stack[-1]["separated"] = True
            elif kind == "end" and stack:
                current = stack.pop()
                fields.append(FieldInfo(current["id"], "".join(current["code"]), "".join(current["result"]), True))
        elif node.tag == qn("w:instrText") and stack:
            stack[-1]["code"].append(node.text or "")
        elif node.tag == qn("w:t"):
            for current in stack:
                if current["separated"]:
                    current["result"].append(node.text or "")
    for current in stack:
        fields.append(FieldInfo(current["id"], "".join(current["code"]), "".join(current["result"]), False))
    return tuple(fields)


def _effective_format(run: Run, paragraph: Paragraph, name: str):
    direct = getattr(run.font, name)
    if direct is not None:
        return direct
    for initial in (run.style, paragraph.style):
        style = initial
        visited = set()
        while style is not None and style.style_id not in visited:
            visited.add(style.style_id)
            value = getattr(style.font, name)
            if value is not None:
                return value
            style = style.base_style
    return None


def _paragraph_property(paragraph: Paragraph, name: str):
    value = getattr(paragraph.paragraph_format, name)
    if value is not None:
        return value
    style = paragraph.style
    seen = set()
    while style is not None and style.style_id not in seen:
        seen.add(style.style_id)
        value = getattr(style.paragraph_format, name)
        if value is not None:
            return value
        style = style.base_style
    return None


def _paragraph_properties(paragraph: Paragraph) -> dict:
    """Resolve explicit paragraph/style properties; unknown is never a guessed value."""
    properties = [paragraph._p.pPr]
    style = paragraph.style
    seen = set()
    while style is not None and style.style_id not in seen:
        seen.add(style.style_id)
        properties.append(style.element.pPr)
        style = style.base_style
    unsupported_indent = any(
        item is not None and any(
            node.get(qn(attribute)) is not None
            for node in item.findall(qn("w:ind"))
            for attribute in ("w:firstLineChars", "w:hangingChars", "w:leftChars", "w:startChars")
        ) for item in properties
    )
    first_line = _paragraph_property(paragraph, "first_line_indent")
    left = _paragraph_property(paragraph, "left_indent")
    spacing = _paragraph_property(paragraph, "line_spacing")
    mode = _paragraph_property(paragraph, "line_spacing_rule")
    if spacing is None:
        spacing_value = spacing_mode = None
    elif mode == WD_LINE_SPACING.EXACTLY:
        spacing_value, spacing_mode = spacing.pt, "exact"
    elif mode == WD_LINE_SPACING.AT_LEAST:
        spacing_value, spacing_mode = spacing.pt, "at_least"
    elif isinstance(spacing, float):
        spacing_value, spacing_mode = spacing, "multiple"
    else:
        spacing_value = spacing_mode = None
    return {
        "first_line_indent_mm": None if first_line is None or unsupported_indent else round(first_line.mm, 3),
        "left_indent_mm": None if left is None or unsupported_indent else round(left.mm, 3),
        "line_spacing_value": spacing_value, "line_spacing_mode": spacing_mode,
        "numbered": any(item is not None and item.find(qn("w:numPr")) is not None for item in properties),
    }


def _inline_runs(paragraph: Paragraph) -> Iterator[tuple[Run, str | None]]:
    """Read visible run containers, including legacy smart tags, as untrusted data.

    Do not enter drawings, deleted/inserted revisions, field instructions or
    arbitrary XML. Cached field values are read, never executed or refreshed.
    """
    containers = {qn(name) for name in ("w:smartTag", "w:sdt", "w:sdtContent", "w:customXml", "w:fldSimple")}

    def walk(element, target: str | None = None, depth: int = 0):
        if depth > 64:
            raise DocumentParseError("Слишком глубокая вложенность встроенного текста Word")
        for child in element:
            if child.tag == qn("w:r"):
                yield Run(child, paragraph), target
            elif child.tag == qn("w:hyperlink"):
                yield from walk(child, Hyperlink(child, paragraph).url or None, depth + 1)
            elif child.tag in containers:
                yield from walk(child, target, depth + 1)

    yield from walk(paragraph._p)


def _parse_docx(data: bytes) -> ParsedDocument:
    _check_zip(data)
    try:
        document = Document(BytesIO(data))
    except Exception as exc:
        raise DocumentParseError("Не удалось прочитать документ DOCX") from exc
    paragraphs: list[ParagraphInfo] = []
    tables: list[TableInfo] = []

    def walk(container, element, path: str, location: str, depth: int = 0, section_index: int = 0) -> None:
        if depth > 20:
            raise DocumentParseError("Превышена допустимая вложенность таблиц DOCX")
        block_index = 0
        for child in element:
            if child.tag == qn("w:p"):
                paragraph = Paragraph(child, container)
                fragment_id = f"{path}/p[{block_index}]"
                raw_runs = list(_inline_runs(paragraph))
                runs = []
                for index, (run, target) in enumerate(raw_runs):
                    effective_size = _effective_format(run, paragraph, "size")
                    runs.append(RunInfo(
                        fragment_id=f"{fragment_id}/r[{index}]", text=run.text,
                        font_name=run.font.name,
                        font_size_pt=run.font.size.pt if run.font.size is not None else None,
                        bold=run.bold, italic=run.italic,
                        underline=None if run.underline is None else bool(run.underline),
                        hyperlink_target=target, effective_font_name=_effective_format(run, paragraph, "name"),
                        effective_font_size_pt=effective_size.pt if effective_size is not None else None,
                        effective_bold=_effective_format(run, paragraph, "bold"),
                        effective_italic=_effective_format(run, paragraph, "italic"),
                    ))
                paragraphs.append(ParagraphInfo(
                    index=len(paragraphs), text="".join(run.text for run, _ in raw_runs),
                    font_names=tuple(sorted({run.font_name for run in runs if run.font_name})),
                    fragment_id=fragment_id, location=location,
                    style=paragraph.style.style_id if paragraph.style is not None else None,
                    alignment=paragraph.alignment.name if paragraph.alignment is not None else None,
                    runs=tuple(runs), fields=_read_fields(paragraph, fragment_id), section_index=section_index,
                    heading_level=_heading_level(paragraph),
                    image_count=sum(1 for node in child.iter() if node.tag in {qn("a:blip"), "{urn:schemas-microsoft-com:vml}imagedata"}),
                    **_paragraph_properties(paragraph),
                ))
                if path == "body" and child.pPr is not None and child.pPr.sectPr is not None:
                    section_index += 1
                block_index += 1
            elif child.tag == qn("w:tbl"):
                table = Table(child, container)
                table_id = f"{path}/table[{block_index}]"
                tables.append(TableInfo(table_id, len(table.rows), len(table.columns)))
                seen_cells: set = set()
                for row_index, row in enumerate(table.rows):
                    for column_index, cell in enumerate(row.cells):
                        if cell._tc in seen_cells:
                            continue
                        seen_cells.add(cell._tc)
                        walk(cell, cell._tc, f"{table_id}/row[{row_index}]/cell[{column_index}]", location, depth + 1, section_index)
                block_index += 1

    walk(document, document.element.body, "body", "body")
    sections = []
    for index, section in enumerate(document.sections):
        def millimeters(value) -> float | None:
            return round(value.mm, 3) if value is not None else None

        sections.append(SectionInfo(index, *(
            millimeters(getattr(section, name)) for name in (
                "page_width", "page_height", "top_margin", "bottom_margin", "left_margin", "right_margin"
            )
        ), gutter_mm=millimeters(section.gutter),
            mirrored_margins=document.settings.element.find(qn("w:mirrorMargins")) is not None))
        for name in ("header", "footer", "first_page_header", "first_page_footer", "even_page_header", "even_page_footer"):
            part = getattr(section, name)
            # Linked sections reuse an earlier definition; do not count it twice or
            # create absent header/footer parts while reading the document.
            if not part.is_linked_to_previous:
                walk(part, part._element, f"section[{index}]/{name}", name, section_index=index)
    text = "\n".join(p.text for p in paragraphs if p.text.strip())
    return ParsedDocument(paragraphs=paragraphs, text=text,
                          paragraph_count=len(document.paragraphs), tables_count=len(document.tables),
                          sections=tuple(sections), tables=tuple(tables))


def _heading_level(paragraph: Paragraph) -> int | None:
    """Read OOXML outline levels without guessing visual layout or executing fields."""
    properties = [paragraph._p.pPr]
    style = paragraph.style
    seen = set()
    while style is not None and style.style_id not in seen:
        seen.add(style.style_id)
        properties.append(style.element.pPr)
        style = style.base_style
    for item in properties:
        if item is not None:
            level = item.find(qn("w:outlineLvl"))
            if level is not None:
                value = level.get(qn("w:val"), "9")
                if value.isdigit() and 0 <= int(value) <= 8:
                    return int(value) + 1
                return None
    return None


def parse_docx(data: bytes) -> ParsedDocument:
    try:
        return _parse_docx(data)
    except DocumentParseError:
        raise
    except Exception as exc:
        raise DocumentParseError("Не удалось разобрать структуру DOCX") from exc


def parse_knowledge(filename: str, data: bytes) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix == ".docx":
        return parse_docx(data).text
    if suffix == ".txt":
        try:
            return data.decode("utf-8-sig")
        except UnicodeError as exc:
            raise DocumentParseError("TXT должен иметь кодировку UTF-8") from exc
    raise DocumentParseError("Для базы знаний поддерживаются только DOCX и TXT")
