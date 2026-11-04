"""Deterministic implementations of explicitly versioned prototype rules."""
import re

from app.diagnostics import location
from app.document_parser import ParsedDocument
from app.rule_registry import RuleSet, RuleSpec, load_registry
from app.schemas import DocumentType, Finding


def applicable_rules(
    registry: RuleSet, document_type: DocumentType, template_id: str | None,
    enabled_codes: set[str] | None = None,
    policy_profile: str | None = None,
) -> list[RuleSpec]:
    return [rule for rule in registry.rules
            if rule.applicability.applies(document_type, template_id, policy_profile)
            and (enabled_codes is None or rule.code in enabled_codes)]


def inspect_document(
    doc: ParsedDocument, document_type: DocumentType = DocumentType.unspecified,
    template_id: str | None = None, *, registry: RuleSet | None = None,
    enabled_codes: set[str] | None = None,
) -> list[Finding]:
    findings: list[Finding] = []
    for rule in applicable_rules(registry or load_registry(), document_type, template_id, enabled_codes):
        def finding(evidence: str = "", fragment_id: str | None = None,
                    message: str | None = None, recommendation: str | None = None) -> Finding:
            return Finding(
                code=rule.code, category=rule.category, severity=rule.severity,
                message=(message or rule.description)[:1000],
                recommendation=(recommendation or rule.recommendation)[:1000],
                evidence=evidence[:600], source_ref=rule.source.ref,
                fragment_id=fragment_id, rule_version=rule.version,
            )

        paragraphs = [p for p in doc.paragraphs if p.location in rule.applicability.locations]
        if rule.implementation == "empty_text":
            if not doc.text.strip():
                findings.append(finding(message="Из DOCX не извлечён непустой текст: текстовые проверки выполнить нельзя.",
                    recommendation="Откройте файл в Word и проверьте, можно ли выделить текст. Если письмо состоит из изображений, подготовьте DOCX с распознанным текстом: OCR в сервисе не подключён."))
        elif rule.implementation == "phrase":
            for paragraph in paragraphs:
                match = next((match for pattern in rule.patterns
                              if (match := re.search(re.escape(pattern), paragraph.text, re.IGNORECASE))), None)
                if match:
                    phrase = match.group(0)
                    place = location(paragraph.fragment_id).capitalize()
                    message = recommendation = None
                    if rule.code == 'CONTENT_VAGUE_DEADLINE':
                        message = f'{place}: фраза «{phrase}» сама по себе не задаёт дату или длительность выполнения.'
                        recommendation = 'Проверьте, указан ли согласованный срок в другом месте письма или переписке. Если срок обязателен и не определён, замените эту фразу подтверждённой датой или периодом; дату автоматически не подставляем.'
                    elif rule.code == 'CONTENT_VAGUE_OBJECT':
                        message = f'{place}: выражение «{phrase}» само по себе не перечисляет запрашиваемые документы.'
                        recommendation = 'Проверьте, определён ли перечень в соседнем тексте, приложении или предыдущем письме. Если нет, перечислите документы либо укажите точную ссылку на существующий перечень.'
                    findings.append(finding(phrase, paragraph.fragment_id or None, message, recommendation))
                    break
        elif rule.implementation == "long_paragraph":
            for paragraph in paragraphs:
                if len(paragraph.text.strip()) > rule.threshold:
                    findings.append(finding(paragraph.text[:130], paragraph.fragment_id or None,
                        message=f'{location(paragraph.fragment_id).capitalize()}: в абзаце {len(paragraph.text.strip())} символов при демонстрационном пороге {rule.threshold}. Это сигнал читаемости, а не нарушение инструкции.',
                        recommendation='Найдите абзац по цитате в evidence. Если он содержит несколько самостоятельных мыслей, разделите его по смыслу. Само превышение порога не требует обязательной правки.'))
        elif rule.implementation == "many_fonts":
            fonts = {font for paragraph in paragraphs for font in paragraph.font_names}
            if len(fonts) >= rule.threshold:
                names = ', '.join(name[:100] for name in sorted(fonts)[:8])
                if len(fonts) > 8:
                    names += f', ещё {len(fonts) - 8}'
                findings.append(finding(', '.join(sorted(fonts)),
                    message=f'В распознанном тексте явно заданы {len(fonts)} различных шрифтов: {names}. Порог эвристики — {rule.threshold}.',
                    recommendation='Сравните гарнитуры основного текста, таблиц и реквизитов. Исправляйте только необоснованные различия: разрешённые шрифты таблиц и специальные реквизиты нельзя автоматически унифицировать.'))
    return findings
