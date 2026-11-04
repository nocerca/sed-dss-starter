import asyncio
from urllib.parse import parse_qs

import httpx
import pytest
from test_document_context import parsed

from app.config import Settings
from app.language import LanguageCheckError, check_language, decode_matches


def settings(**options):
    return Settings(_env_file=None, language_tool_enabled=True, **options)


def install(monkeypatch, handler):
    original = httpx.AsyncClient
    monkeypatch.setattr("app.language.httpx.AsyncClient", lambda **kwargs: original(
        **kwargs, transport=httpx.MockTransport(handler)
    ))


@pytest.mark.parametrize("url", ["https://api.languagetool.org/v2", "http://public.example/v2",
                                  "http://localhost/v2?secret=x", "http://u:p@localhost/v2"])
def test_public_or_credentialed_language_endpoints_are_rejected_without_network(monkeypatch, url):
    def forbidden(**kwargs):
        pytest.fail("A public language endpoint must never receive a document")
    monkeypatch.setattr("app.language.httpx.AsyncClient", forbidden)
    with pytest.raises(LanguageCheckError):
        asyncio.run(check_language(settings(language_tool_base_url=url), parsed("Закрытый текст")))


def test_local_language_transport_and_utf16_offsets(monkeypatch):
    d = parsed("😀 ашипка")
    def handler(request):
        assert request.url.host == "languagetool" and request.url.path == "/v2/check"
        form = parse_qs(request.content.decode())
        assert form["language"] == ["ru"] and form["text"] == [d.text]
        return httpx.Response(200, json={"software": {"version": "6.6"}, "matches": [{
            "offset": 3, "length": 6, "message": "Возможная орфографическая ошибка",
            "rule": {"id": "MORFOLOGIK_RULE_RU_RU"}, "replacements": [{"value": "ошибка"}],
        }]})
    install(monkeypatch, handler)
    result = asyncio.run(check_language(settings(), d))
    assert len(result.findings) == 1
    assert result.findings[0].evidence == "ашипка"
    assert result.findings[0].method == "language_tool"
    assert result.findings[0].fragment_id == "body/p[0]" and not result.findings[0].verified


def test_malformed_offsets_do_not_create_invented_quotes():
    d = parsed("Текст")
    payload = {"matches": [{"offset": 999, "length": 10}, {"offset": True, "length": 2}]}
    assert decode_matches(payload, d.text, d) == []


@pytest.mark.parametrize("status", [302, 401, 500])
def test_language_errors_do_not_expose_response_body(monkeypatch, status):
    install(monkeypatch, lambda request: httpx.Response(status, text="PRIVATE_SOURCE SECRET"))
    with pytest.raises(LanguageCheckError) as error:
        asyncio.run(check_language(settings(), parsed("Текст")))
    assert "PRIVATE_SOURCE" not in str(error.value) and "SECRET" not in str(error.value)


def test_language_excerpt_is_bounded_in_utf16_and_reports_truncation(monkeypatch):
    def handler(request):
        text = parse_qs(request.content.decode())["text"][0]
        assert len(text.encode("utf-16-le")) <= 40000
        assert "PRIVATE_TAIL" not in text
        return httpx.Response(200, json={"software": {"version": "6.6"}, "matches": []})
    install(monkeypatch, handler)
    result = asyncio.run(check_language(settings(), parsed("😀" * 10001 + "PRIVATE_TAIL")))
    assert result.truncated and result.checked_characters == 10000
