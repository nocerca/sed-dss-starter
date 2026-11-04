"""Local-only LanguageTool adapter. No public endpoint or model fallback."""

import json
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

from app.config import Settings
from app.document_context import fragment_for_evidence
from app.document_parser import ParsedDocument
from app.schemas import Finding

MAX_TEXT_UNITS = 20000
MAX_RESPONSE_BYTES = 1024 * 1024


class LanguageCheckError(RuntimeError):
    """Safe diagnostic, never upstream bodies, URLs, source text or credentials."""


@dataclass(frozen=True)
class LanguageResult:
    findings: list[Finding]
    checked_characters: int
    truncated: bool
    engine_version: str


def local_endpoint(settings: Settings) -> str:
    if not settings.language_tool_enabled:
        raise LanguageCheckError("Локальные языковые проверки выключены; нужен профиль Docker language и LANGUAGE_TOOL_ENABLED=true")
    try:
        url = urlsplit(settings.language_tool_base_url)
        valid = (url.scheme == "http" and url.hostname in {"languagetool", "localhost", "127.0.0.1", "::1"}
                 and not url.username and not url.password and not url.query and not url.fragment
                 and url.path.rstrip("/") == "/v2")
        _ = url.port
    except ValueError:
        valid = False
    if not valid:
        raise LanguageCheckError("LanguageTool допускается только как локальный сервис; внешний адрес запрещён")
    return settings.language_tool_base_url.rstrip("/") + "/check"


def decode_matches(payload: dict, text: str, document: ParsedDocument) -> list[Finding]:
    matches = payload.get("matches")
    if not isinstance(matches, list):
        raise LanguageCheckError("Некорректная схема ответа локального языкового модуля")
    encoded = text.encode("utf-16-le")
    findings = []
    seen = set()
    for match in matches[:100]:
        if not isinstance(match, dict):
            continue
        offset, length = match.get("offset"), match.get("length")
        if type(offset) is not int or type(length) is not int or offset < 0 or length <= 0:
            continue
        if (offset + length) * 2 > len(encoded):
            continue
        try:
            evidence = encoded[offset * 2:(offset + length) * 2].decode("utf-16-le")
        except UnicodeError:
            continue
        if not evidence.strip() or evidence not in text:
            continue
        rule = match.get("rule", {})
        if not isinstance(rule, dict):
            continue
        rule_id = rule.get("id")
        message = match.get("message")
        if not isinstance(rule_id, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,75}", rule_id):
            continue
        if not isinstance(message, str) or len(message) < 3:
            continue
        key = (rule_id, offset, length)
        if key in seen:
            continue
        seen.add(key)
        replacements = match.get("replacements", [])
        suggestions = [r["value"][:80] for r in replacements[:3]
                       if isinstance(r, dict) and isinstance(r.get("value"), str)] if isinstance(replacements, list) else []
        findings.append(Finding(
            code=("LT_" + rule_id)[:90], category="language", severity="info",
            message=message[:1000], evidence=evidence[:600],
            recommendation=("Возможные варианты: " + "; ".join(suggestions)) if suggestions else "Проверьте формулировку с учётом контекста.",
            source_ref=f"LanguageTool:6.6:{rule_id}", method="language_tool", verified=False,
            fragment_id=fragment_for_evidence(document, evidence), rule_version="6.6",
        ))
    return findings


async def check_language(settings: Settings, document: ParsedDocument) -> LanguageResult:
    endpoint = local_endpoint(settings)
    excerpt = document.text.encode("utf-16-le")[:MAX_TEXT_UNITS * 2].decode("utf-16-le", errors="ignore")
    if not excerpt.strip():
        return LanguageResult([], len(excerpt), False, "6.6")
    try:
        async with httpx.AsyncClient(timeout=settings.language_tool_timeout_seconds,
                                     trust_env=False, follow_redirects=False) as client:
            async with client.stream("POST", endpoint, data={"language": "ru", "text": excerpt}) as response:
                response.raise_for_status()
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > MAX_RESPONSE_BYTES:
                        raise LanguageCheckError("Ответ локального языкового модуля слишком большой")
        payload = json.loads(body)
        if not isinstance(payload, dict) or not str(payload.get("software", {}).get("version", "")).startswith("6.6"):
            raise LanguageCheckError("Ожидался локальный LanguageTool 6.6")
        return LanguageResult(decode_matches(payload, excerpt, document), len(excerpt),
                              len(excerpt) < len(document.text), "6.6")
    except (httpx.HTTPError, ValueError, TypeError, AttributeError) as exc:
        raise LanguageCheckError("Локальный языковой модуль не завершил проверку") from exc
