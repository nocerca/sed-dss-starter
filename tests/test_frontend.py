"""Public static shell does not expose private configuration or replace REST auth."""

from html.parser import HTMLParser

from app.config import get_settings


class AssetParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.sources = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == 'script' or tag == 'link' and values.get('rel') in {'stylesheet', 'icon'}:
            self.sources.append(values.get('src') or values.get('href'))


def test_static_client_served_without_key_and_without_configuration(client, monkeypatch):
    sentinel = 'PRIVATE_FRONTEND_SECRET_SENTINEL'
    monkeypatch.setattr(get_settings(), 'app_api_key', sentinel)
    monkeypatch.setattr(get_settings(), 'openclaw_api_key', sentinel)
    response = client.get('/')
    assert response.status_code == 200 and 'text/html' in response.headers['content-type']
    assert 'Проверка письма' in response.text and sentinel not in response.text
    assert response.headers['cache-control'] == 'no-store'
    policy = response.headers['content-security-policy']
    assert "connect-src 'self'" in policy and "script-src 'self'" in policy
    assert 'unsafe-inline' not in policy and 'unsafe-eval' not in policy
    assert response.headers['x-frame-options'] == 'DENY'
    assert response.headers['referrer-policy'] == 'no-referrer'
    parser = AssetParser()
    parser.feed(response.text)
    assert parser.sources
    for source in parser.sources:
        assert source.startswith('/assets/')
        asset = client.get(source)
        assert asset.status_code == 200 and sentinel not in asset.text
        assert asset.headers['x-content-type-options'] == 'nosniff'
        if source.endswith('.mjs'):
            assert asset.headers['content-type'].startswith('text/javascript')


def test_frontend_cannot_shadow_rest_or_expose_project_files(client, headers):
    assert client.get('/api/v1/reports').status_code == 401
    response = client.get('/api/v1/capabilities', headers=headers)
    assert response.status_code == 200 and response.json()['sed_integration'] == 'REST API'
    for path in ['/.env', '/assets/.env', '/assets/%2e%2e/config.py', '/unknown-page']:
        assert client.get(path).status_code == 404
    schema = client.get('/openapi.json').json()
    assert '/' not in schema['paths'] and '/assets' not in schema['paths']
    assert '/api/v1/analyze' in schema['paths']
