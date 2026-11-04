"""Conservative, local checks against an explicitly selected organizational profile."""

import re
from collections import Counter
from dataclasses import dataclass
from datetime import date

from app.diagnostics import CHECK_ROLES, policy_text
from app.document_parser import ParagraphInfo, ParsedDocument
from app.rule_registry import RuleSpec
from app.schemas import Finding

PROFILE_ID = "office_instruction_letters"
MAX_RESULTS = 2000
MAX_FINDINGS = 200
IMPLEMENTED = (
    "ORG_MARGINS", "ORG_BODY_FONT", "ORG_BODY_FONT_SIZE", "ORG_BODY_LINE_SPACING",
    "ORG_BODY_FIRST_LINE", "ORG_REFERENCE_DATA_FONT", "ORG_RECIPIENT_FONT",
    "ORG_SUBJECT_FONT", "ORG_ATTACHMENT_FONT", "ORG_SIGNATURE_FONT",
    "ORG_EXECUTOR_FONT", "ORG_EXECUTOR_SPACING", "ORG_DATE_FORMAT",
    "ORG_EXECUTOR_CONTACT", "ORG_SIGNATURE_FORBIDDEN_PREFIX", "ORG_RECIPIENT_COPY_LABEL",
)
FULL_NAME = r"[А-ЯЁ][а-яё-]+\s+[А-ЯЁ][а-яё-]+\s+[А-ЯЁ][а-яё-]+"
BODY_START = re.compile(r"^(?:Просим|Прошу|Сообщаем|Сообщаю|Направляем|Направляю|"
                        r"Уведомляем|Предлагаем|Благодарим|В соответствии|В ответ на)\b", re.I)
PHONE = re.compile(r"\+?\d[\d ()-]{4,}\d")


@dataclass(frozen=True)
class Roles:
    blocks: dict[str, tuple[ParagraphInfo, ...]]
    basis: dict[str, str]


def identify_roles(document: ParsedDocument) -> Roles:
    """Use visible layout/text anchors, never instructions or custom role tags in uploads."""
    body = [p for p in document.paragraphs if p.location == "body" and p.text.strip()]
    blocks: dict[str, list[ParagraphInfo]] = {key: [] for key in (
        "body", "recipient", "reference_data", "subject", "attachment", "signature", "executor", "date",
    )}
    basis = {}
    greetings = [i for i, p in enumerate(body) if re.match(r"^Уважаем\w*\b.*!\s*$", p.text.strip())]
    starts = [i for i, p in enumerate(body) if BODY_START.match(p.text.strip())]
    start = greetings[0] + 1 if len(greetings) == 1 else (starts[0] if starts else None)
    signature = [i for i, p in enumerate(body) if re.match(
        r"^(?:За\s+|/\s*)?(?:Ректор|Проректор|Директор|Начальник|Руководитель|Заведующий|Заместитель)\b",
        p.text.strip(), re.I,
    ) and i > (start if start is not None else len(body))]
    for i, p in enumerate(body):
        text = p.text.strip()
        if re.match(r"^Приложени[ея]\s*:", text, re.I):
            blocks["attachment"].append(p)
        if re.match(r"^(?:О|Об)\s+\S", text) and (start is None or i < start):
            blocks["subject"].append(p)
        if start is not None and i < start:
            if re.match(r"^(?:от\s+)?\d.{3,60}?(?:\s*№.*)?$", text, re.I) and (
                "№" in text or re.fullmatch(r"\d[\d./ -]{5,12}(?:\s*г\.?)?", text)
                or re.fullmatch(r"\d{1,2}\s+[а-яё]+\s+\d{4}\s*г\.", text, re.I)
            ):
                blocks["date"].append(p)
            if (re.match(r"^(?:Генеральному директору|Директору|Ректору|Руководителю|Начальнику|Копия\s*:?)\b", text, re.I)
                    and not text.startswith("На №")):
                blocks["recipient"].append(p)
            if re.search(r"\b(?:ИНН|КПП)\b", text) and ("@" in text or PHONE.search(text)):
                blocks["reference_data"].append(p)
    if start is not None:
        stops = [i for i in range(start, len(body)) if (
            re.match(r"^(?:С уважением|Приложени[ея]\s*:|Исполнитель\s*:)", body[i].text.strip(), re.I)
            or i in signature
        )]
        end = min(stops, default=len(body))
        blocks["body"] = [p for p in body[start:end]
                          if not p.heading_level and not p.image_count and not p.fields]
        basis["body"] = "Уникальное приветствие либо распознанное начало делового текста; до подписи/приложений"
    if len(signature) == 1:
        blocks["signature"].append(body[signature[0]])
    # Executor can be in the body, a table, or a footer. Do not equate footer absence with absence.
    for p in document.paragraphs:
        text = p.text.strip()
        if re.match(r"^Исполнитель\s*:", text, re.I) or (
            "footer" in p.location and re.match(FULL_NAME + r"(?:\s|$)", text)
        ):
            blocks["executor"].append(p)
    for role in blocks:
        basis.setdefault(role, "Консервативный текстовый признак реквизита; не экспертное подтверждение роли")
    return Roles({key: tuple(value) for key, value in blocks.items()}, basis)


def _font(family: str | None) -> str | None:
    if family is None:
        return None
    return re.sub(r"\s*\(cyr\)\s*$", "", family.casefold()).strip()


def _uniform_body_font(roles: Roles) -> tuple[str, float] | None:
    fonts = {(run.effective_font_name, run.effective_font_size_pt)
             for p in roles.blocks["body"] for run in p.runs if run.text.strip()}
    if len(fonts) != 1:
        return None
    family, size = next(iter(fonts))
    return (family, size) if family is not None and size is not None else None


def _date_valid(text: str) -> bool:
    text = re.split(r"\s*№", re.sub(r"^от\s+", "", text.strip(), flags=re.I), maxsplit=1)[0].strip()
    numeric = re.fullmatch(r"(\d{2})\.(\d{2})\.(\d{4})", text)
    months = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа",
              "сентября", "октября", "ноября", "декабря"]
    verbal = re.fullmatch(r"([1-9]|[12]\d|3[01])\s+(" + "|".join(months) + r")\s+(\d{4})\s+г\.", text, re.I)
    try:
        if numeric:
            day, month, year = map(int, numeric.groups())
        elif verbal:
            day, month, year = int(verbal[1]), months.index(verbal[2].casefold()) + 1, int(verbal[3])
        else:
            return False
        date(year, month, day)
        return True
    except ValueError:
        return False


def evaluate_policy(document: ParsedDocument, rules: list[RuleSpec]) -> tuple[list[Finding], dict]:
    roles = identify_roles(document)
    findings = []
    outcomes = []
    body_font = _uniform_body_font(roles)
    truncated = False

    def record(rule: RuleSpec, fragment: str | None, expected: dict, observed: dict,
               valid: bool | None, reason: str):
        nonlocal truncated
        if len(outcomes) >= MAX_RESULTS:
            truncated = True
            return
        reason += ". " + rule.policy_specification["implementation_scope"]
        status = "insufficient_data" if valid is None else ("pass" if valid else "violation")
        message, recommendation = policy_text(rule.code, fragment, expected, observed, status)
        outcomes.append({"code": rule.code, "rule_version": rule.version, "outcome": status,
                         "fragment_id": fragment, "expected": expected, "observed": observed,
                         "reason": (message + ' ' + reason) if valid is None else reason,
                         "message": message, "recommendation": recommendation,
                         "source_ref": rule.source.ref})
        if valid is False:
            if len(findings) >= MAX_FINDINGS:
                truncated = True
                return
            findings.append(Finding(
                code=rule.code, category=rule.category, severity=rule.severity,
                message=message[:1000], recommendation=recommendation[:1000],
                evidence=message[:600],
                fragment_id=fragment, source_ref=rule.source.ref, rule_version=rule.version,
                expected=expected, observed=observed, applicability_reason=reason,
            ))

    for rule in rules:
        if rule.implementation != "letter_policy":
            continue
        spec = rule.policy_specification
        expected = spec["expected"]
        if rule.code == "ORG_MARGINS":
            if not document.sections:
                record(rule, None, expected, {}, None, "Не извлечены свойства разделов")
            for section in document.sections:
                observed = {key: getattr(section, key.replace("_mm", "_margin_mm")) for key in expected}
                known = all(value is not None for value in observed.values())
                valid = all(abs(observed[key] - value) <= 0.1 for key, value in expected.items()) if known else None
                if section.mirrored_margins or section.gutter_mm:
                    valid = None
                observed.update(mirrored_margins=section.mirrored_margins, gutter_mm=section.gutter_mm)
                record(rule, f"section[{section.index}]", expected, observed, valid,
                       "Выбран профиль организации; поля раздела в мм. Зеркальные поля/переплёт требуют раскладки")
            continue
        role = CHECK_ROLES[rule.code]
        paragraphs = roles.blocks[role]
        if not paragraphs:
            record(rule, None, expected, {}, None, f"Не установлена роль {role}; отсутствие реквизита не доказано")
            continue
        for paragraph in paragraphs:
            observed = {}
            valid = None
            if rule.code.endswith("_FONT") or rule.code == "ORG_BODY_FONT_SIZE":
                for run in paragraph.runs:
                    if not run.text.strip():
                        continue
                    observed = {"family": run.effective_font_name, "size_pt": run.effective_font_size_pt,
                                "bold": run.effective_bold}
                    if rule.code == "ORG_BODY_FONT":
                        valid = None if observed["family"] is None else _font(observed["family"]) == "times new roman"
                    elif rule.code == "ORG_BODY_FONT_SIZE":
                        valid = None if observed["size_pt"] is None else 10 <= observed["size_pt"] <= 14
                    elif "match" in expected:
                        valid = None if body_font is None or None in (observed["family"], observed["size_pt"]) else (
                            _font(observed["family"]) == _font(body_font[0]) and observed["size_pt"] == body_font[1])
                    else:
                        properties = [
                            None if observed["family"] is None else _font(observed["family"]) == "times new roman",
                            None if observed["size_pt"] is None else observed["size_pt"] == expected["size_pt"],
                            None if observed["bold"] is None else observed["bold"] is False,
                        ]
                        valid = False if False in properties else (None if None in properties else True)
                    actual_expected = {**expected, "family": body_font[0], "size_pt": body_font[1]} if "match" in expected and body_font else expected
                    record(rule, run.fragment_id, actual_expected, observed, valid, roles.basis[role])
                continue
            if rule.code == "ORG_BODY_FIRST_LINE":
                observed = {"first_line_mm": paragraph.first_line_indent_mm,
                            "left_indent_mm": paragraph.left_indent_mm}
                if paragraph.first_line_indent_mm is not None and not paragraph.numbered and not paragraph.text.startswith("\t") and "/table[" not in paragraph.fragment_id:
                    valid = abs(paragraph.first_line_indent_mm + (paragraph.left_indent_mm or 0) - 12.5) <= 0.1
            elif rule.code in {"ORG_BODY_LINE_SPACING", "ORG_EXECUTOR_SPACING"}:
                value, mode = paragraph.line_spacing_value, paragraph.line_spacing_mode
                observed = {"mode": mode, "value": value}
                if value is not None:
                    if rule.code == "ORG_EXECUTOR_SPACING":
                        valid = mode == "exact" and abs(value - 12) < 0.01
                    elif mode == "exact" and value <= 12:
                        valid = value == 12
                    elif mode == "multiple" and value > 2:
                        valid = False
            elif rule.code == "ORG_DATE_FORMAT":
                valid = _date_valid(paragraph.text)
                observed = {"date_text": re.split(r"\s*№", paragraph.text, maxsplit=1)[0].strip()[:70]}
            elif rule.code == "ORG_EXECUTOR_CONTACT":
                area = paragraph.fragment_id.rsplit("/p[", 1)[0]
                nearby = [p for p in document.paragraphs if p.index >= paragraph.index and p.index <= paragraph.index + 2
                          and p.fragment_id.rsplit("/p[", 1)[0] == area]
                text = " ".join(p.text for p in nearby)
                observed = {"full_name_found": bool(re.search(FULL_NAME, paragraph.text)),
                            "phone_found": bool(PHONE.search(text))}
                # Only check contact fields. Physical last-page placement is not inferred.
                valid = all(observed.values()) if not any(p.image_count for p in nearby) else None
                observed['image_references'] = sum(p.image_count for p in nearby)
            elif rule.code == "ORG_SIGNATURE_FORBIDDEN_PREFIX":
                observed = {"forbidden_prefix_found": bool(re.match(r"^(?:За\s+|/)", paragraph.text.strip(), re.I))}
                valid = not observed["forbidden_prefix_found"]
                if match := re.match(r'^(За\s+|/)', paragraph.text.strip(), re.I):
                    observed['forbidden_prefix'] = match[0].strip()
            elif rule.code == "ORG_RECIPIENT_COPY_LABEL":
                observed = {"copy_label_found": bool(re.match(r"^Копия\b", paragraph.text.strip(), re.I))}
                # A label alone does not establish that this is recipient 2..4.
                valid = None if observed["copy_label_found"] else True
            record(rule, paragraph.fragment_id, expected, observed, valid, roles.basis[role])
    counts = Counter(result["outcome"] for result in outcomes)
    return findings, {
        "profile": PROFILE_ID, "results": outcomes, "counts": dict(counts), "truncated": truncated,
        "roles": {role: {"fragments": [p.fragment_id for p in paragraphs[:200]],
                         "total_fragments": len(paragraphs), "basis": roles.basis[role]}
                  for role, paragraphs in roles.blocks.items()},
        "limitations": ["Роли определены консервативными текстовыми признаками, требуют проверки человеком",
                        "Геометрия страниц, полномочия и стадия подписания не проверялись"],
    }
