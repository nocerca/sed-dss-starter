"""Bounded structural context and conservative genre signals, never document commands."""

import re

from app.document_parser import ParsedDocument

MAX_LLM_CHARACTERS = 12000
MAX_STRUCTURE_BLOCKS = 100


def heading_like(text: str) -> bool:
    value = text.strip()
    return 0 < len(value) <= 160 and value.endswith(":")


def document_profile(document: ParsedDocument) -> dict:
    """Warnings only: this is a versioned heuristic, not a trained classifier."""
    start = document.text[:2500].casefold()
    academic_title = bool(re.search(r"(?:лабораторн\w*|курсов\w*|дипломн\w*)\s+работ\w*", start))
    academic_sections = sum(bool(re.search(pattern, document.text.casefold())) for pattern in (
        r"цель работы", r"ход работы|выполнение работы", r"задание", r"выводы?",
    ))
    letter_action = bool(re.search(r"\b(?:просим|направляем|сообщаем|уведомляем|благодарим)\b", start))
    salutation = bool(re.search(r"\bуважаем\w+\b|с уважением", start))
    signals = []
    if academic_title:
        signals.append("academic_title")
    if academic_sections >= 2:
        signals.append("academic_sections")
    if letter_action:
        signals.append("letter_action")
    if salutation:
        signals.append("letter_salutation")
    classification = "likely_non_letter" if academic_title and academic_sections >= 2 else (
        "likely_letter" if letter_action and salutation and not academic_title else "undetermined"
    )
    return {"version": "1.0.0", "classification": classification, "signals": signals}


def model_structure(document: ParsedDocument) -> dict:
    """Only text already in the excerpt, numeric counts and generated structural IDs.

    Do not include filenames, hyperlink targets, field instructions, relationship
    IDs, alternative image text or metadata. Images are counted, never transmitted.
    """
    blocks = []
    cursor = 0
    text_blocks = 0
    included = 0
    for paragraph in document.paragraphs:
        has_text = bool(paragraph.text.strip())
        start = cursor + (1 if has_text and text_blocks else 0)
        end = start + len(paragraph.text) if has_text else cursor
        if has_text:
            text_blocks += 1
            cursor = end
        if start >= MAX_LLM_CHARACTERS or len(blocks) >= MAX_STRUCTURE_BLOCKS:
            continue
        block = {"id": paragraph.fragment_id, "location": paragraph.location,
                 "start": min(start, MAX_LLM_CHARACTERS), "end": min(end, MAX_LLM_CHARACTERS),
                 "images": paragraph.image_count, "heading_level": paragraph.heading_level}
        if has_text and (paragraph.heading_level or heading_like(paragraph.text)):
            block["heading"] = paragraph.text[:min(160, MAX_LLM_CHARACTERS - start)]
        blocks.append(block)
        included += 1
    return {"blocks": blocks, "complete": included == len(document.paragraphs),
            "image_references": sum(p.image_count for p in document.paragraphs),
            "tables": len(document.tables), "images_content_available": False,
            "visual_layout_available": False, "profile": document_profile(document)}


def fragment_for_evidence(document: ParsedDocument, evidence: str) -> str | None:
    matching = [p.fragment_id for p in document.paragraphs if evidence and evidence in p.text]
    return matching[0] if len(matching) == 1 else None
