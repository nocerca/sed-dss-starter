"""Headless browser checks with an isolated DB and synthetic documents only.

Run: python tests/browser/check_frontend.py [--screenshots .research/frontend]
No real model requests: server disables the provider, model UI cases mock HTTP.
"""

import argparse
import copy
import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.create_letter_demos import baseline_document  # noqa: E402

SYNTHETIC_KEY = 'synthetic-browser-test-key'


def check(base_url: str, screenshots: Path | None) -> None:
    document = baseline_document()
    document.paragraphs[7].runs[0].font.name = 'Arial'
    document.paragraphs[7].runs[0].text = 'Просим в кратчайшие сроки направить перечень учебных материалов.'
    stream = io.BytesIO()
    document.save(stream)
    file_payload = {'name': 'browser-letter.docx', 'mimeType':
                    'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                    'buffer': stream.getvalue()}
    if screenshots:
        screenshots.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(viewport={'width': 1440, 'height': 1100},
                                      color_scheme='light', reduced_motion='reduce')
        blocked = []

        def local_only(route):
            if urlsplit(route.request.url).netloc != urlsplit(base_url).netloc:
                blocked.append('external request blocked')
                route.abort()
            else:
                route.continue_()

        context.route('**/*', local_only)
        page = context.new_page()
        errors, posts = [], []
        page.on('pageerror', lambda error: errors.append(type(error).__name__))
        page.on('request', lambda request: posts.append(request) if request.method == 'POST' else None)
        page.goto(base_url)
        expect(page.get_by_role('heading', name='Проверка письма', exact=True)).to_be_visible()
        assert page.locator('#model-check').is_disabled()
        if screenshots:
            page.screenshot(path=str(screenshots / 'light.png'), full_page=True)

        def connect():
            page.locator('#connection-button').click()
            page.locator('#api-key').fill(SYNTHETIC_KEY)
            page.locator('#connect-button').click()
            expect(page.locator('#connection-dialog')).not_to_be_visible()
            expect(page.locator('#connection-label')).to_have_text('Подключено')
            assert page.locator('#api-key').input_value() == ''

        page.locator('#connection-button').click()
        page.locator('#api-key').fill('wrong-synthetic-key')
        page.locator('#connect-button').click()
        expect(page.locator('#connection-error')).to_contain_text('APP_API_KEY')
        page.locator('#api-key').fill(SYNTHETIC_KEY)
        page.locator('#connect-button').click()
        expect(page.locator('#connection-dialog')).not_to_be_visible()
        assert page.locator('#model-check').is_disabled()
        assert page.locator('#language-check').is_disabled()
        print('OK: authentication and unavailable services')

        page.locator('#file-input').set_input_files({'name': 'unsupported.txt', 'mimeType': 'text/plain', 'buffer': b'synthetic'})
        expect(page.locator('#file-error')).to_contain_text('только DOCX')
        page.locator('#file-input').set_input_files(file_payload)
        page.locator('#policy-profile').select_option('office_instruction_letters')
        with page.expect_response(lambda response: '/api/v1/analyze' in response.url) as response:
            page.locator('#analyze-button').click()
        actual_report = response.value.json()
        expect(page.locator('#report-title')).to_have_text('browser-letter.docx')
        expect(page.locator('#findings-list')).to_contain_text('Arial')
        assert any(f['code'] == 'ORG_BODY_FONT' for f in actual_report['findings'])
        assert page.get_by_text('Все результаты проверок', exact=False).locator('..').get_attribute('open') is None
        page.get_by_text('Все результаты проверок', exact=False).click()
        page.get_by_label('Фильтр результатов проверок').select_option('violation')
        expect(page.locator('.audit-row')).to_have_count(1)
        page.get_by_text('Все результаты проверок', exact=False).click()
        with page.expect_download() as download:
            page.get_by_role('button', name='Скачать JSON').click()
        saved = json.loads(Path(download.value.path()).read_text(encoding='utf-8'))
        assert saved['id'] == actual_report['id']
        assert b'name="use_llm"\r\n\r\nfalse' in posts[-1].post_data_buffer
        assert b'name="allow_external_transfer"\r\n\r\nfalse' in posts[-1].post_data_buffer
        if screenshots:
            page.screenshot(path=str(screenshots / 'report.png'), full_page=True)
        print('OK: invalid upload, real DOCX analysis, audit filtering and JSON export')

        page.get_by_role('button', name='Оформление', exact=False).click()
        page.get_by_role('button', name='Разобрать замечание', exact=True).click()
        expect(page.locator('#assistant-answer')).to_contain_text('Локальное объяснение')
        expect(page.locator('.assistant-source')).to_contain_text('ORG_BODY_FONT')
        assert posts[-1].post_data_json['finding_index'] == next(
            index for index, finding in enumerate(actual_report['findings']) if finding['code'] == 'ORG_BODY_FONT')
        assert posts[-1].post_data_json['use_llm'] is False
        page.locator('#assistant-action').select_option('fix')
        page.locator('#assistant-submit').click()
        expect(page.locator('#assistant-answer')).to_contain_text('Главная → Шрифт')
        if screenshots:
            page.screenshot(path=str(screenshots / 'assistant-local.png'), full_page=True)
        page.locator('#assistant-action').select_option('rephrase')
        expect(page.locator('#assistant-submit')).to_be_disabled()
        page.locator('#close-assistant').click()
        page.get_by_role('button', name='Все', exact=False).first.click()
        print('OK: local assistant, Word steps, historical basis and original index after category filtering')

        page.get_by_role('link', name='Отчёты', exact=True).click()
        page.get_by_role('button', name='browser-letter.docx', exact=True).click()
        expect(page.locator('#report-title')).to_have_text('browser-letter.docx')
        page.get_by_role('link', name='Правила', exact=True).click()
        page.locator('#rule-search').fill('ORG_BODY_FONT')
        expect(page.locator('.rule-card')).to_have_count(2)
        page.locator('#rule-filter').select_option('heuristic')
        expect(page.get_by_role('heading', name='Правила не найдены')).to_be_visible()
        page.locator('#rule-filter').select_option('instruction')
        expect(page.locator('.rule-card')).to_have_count(2)
        print('OK: history opens existing reports; rule search and applicability')

        page.get_by_role('link', name='Проверка', exact=True).click()
        page.get_by_role('button', name='Новая проверка', exact=False).click()
        page.set_viewport_size({'width': 390, 'height': 844})
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        if screenshots:
            page.screenshot(path=str(screenshots / 'mobile.png'), full_page=True)
        page.get_by_role('button', name='Включить тёмную тему').click()
        assert page.locator('html').get_attribute('data-theme') == 'dark'
        if screenshots:
            page.screenshot(path=str(screenshots / 'dark-mobile.png'), full_page=True)
        page.set_viewport_size({'width': 1440, 'height': 1100})
        if screenshots:
            page.screenshot(path=str(screenshots / 'dark.png'), full_page=True)
        page.get_by_role('button', name='Включить светлую тему').click()
        print('OK: mobile layout, dark theme and reduced-motion mode')

        def enabled_capabilities(route):
            body = route.fetch().json()
            body['current_provider'] = 'openclaw'
            body['external_llm_enabled'] = True
            route.fulfill(json=body)

        page.route('**/api/v1/capabilities', enabled_capabilities)
        connect()
        mock_posts = []

        def mock_analysis(route):
            mock_posts.append(route.request)
            body = copy.deepcopy(actual_report)
            body['mode'] = 'hybrid'
            body['status'] = 'partial' if len(mock_posts) == 1 else 'completed'
            body['warnings'] = ['Синтетический сбой модельного этапа. Локальные результаты сохранены.']
            body['filename'] = 'letter-<img src=x onerror=window.__xss=1>.docx'
            body['findings'][0]['message'] = '<img src=x onerror=window.__xss=1>'
            route.fulfill(json=body, headers={'Analysis-Cache': 'miss'})

        page.route('**/api/v1/analyze', mock_analysis)
        page.locator('#file-input').set_input_files(file_payload)
        page.locator('#model-check').check()
        before = len(posts)
        page.locator('#analyze-button').click()
        expect(page.locator('#external-consent')).to_be_focused()
        assert len(posts) == before
        page.locator('#external-consent').check()
        page.locator('#analyze-button').click()
        expect(page.locator('#report-screen .notice-warning')).to_contain_text('не полностью')
        expect(page.locator('.warning-list')).to_contain_text('Локальные результаты сохранены')
        assert not page.locator('#external-consent').is_checked()
        assert not page.locator('#report-screen img').count()
        assert page.evaluate('window.__xss === undefined')
        page.get_by_role('button', name='Новая проверка', exact=False).click()
        page.locator('#file-input').set_input_files(file_payload)
        assert not page.locator('#external-consent').is_checked()
        page.locator('#external-consent').check()
        page.locator('#analyze-button').click()
        expect(page.locator('#report-title')).to_be_visible()
        assert len(mock_posts) == 2
        assert mock_posts[0].headers['idempotency-key'] != mock_posts[1].headers['idempotency-key']
        for request in mock_posts:
            assert b'name="allow_external_transfer"\r\n\r\ntrue' in request.post_data_buffer
        print('OK: consent per Send, partial result preserved, fresh keys, XSS rendered as text')

        page.unroute('**/api/v1/analyze', mock_analysis)
        attempts = []

        def uncertain_response(route):
            attempts.append(route.request.headers['idempotency-key'])
            if len(attempts) == 1:
                route.abort('failed')
            else:
                route.continue_()

        page.route('**/api/v1/analyze', uncertain_response)
        page.get_by_role('button', name='Новая проверка', exact=False).click()
        page.locator('#model-check').uncheck()
        page.locator('#file-input').set_input_files(file_payload)
        page.locator('#analyze-button').click()
        expect(page.locator('#analysis-error')).to_contain_text('мог быть принят')
        expect(page.locator('#analyze-button')).to_be_enabled()
        page.locator('#analyze-button').click()
        expect(page.locator('#report-title')).to_have_text('browser-letter.docx')
        assert len(attempts) == 2 and attempts[0] == attempts[1]
        print('OK: uncertain network failure keeps the original idempotency key')

        helper_calls = []
        helper_network_failure = []

        def helper_response(request, **overrides):
            data = request.post_data_json
            return {'report_id': actual_report['id'], 'finding_index': data['finding_index'],
                    'action': data['action'], 'method': 'llm', 'provider': 'openclaw',
                    'status': 'completed', 'answer': '<img src=x onerror=window.__assistant_xss=1>',
                    'steps': [], 'sources': [], 'warnings': ['Синтетический модельный ответ требует проверки.'],
                    'suggestion': None, 'request_id': '00000000-0000-0000-0000-000000000001', **overrides}

        def helper_model(route):
            data = route.request.post_data_json
            if not data['use_llm']:
                route.continue_()
                return
            helper_calls.append(route.request)
            if len(helper_calls) == 1:
                route.fulfill(json=helper_response(route.request, status='insufficient_data'))
            elif data['action'] == 'rephrase':
                route.fulfill(json=helper_response(route.request, suggestion='Просим направить 12 экземпляров до 24.12.2025.'))
            elif not helper_network_failure:
                helper_network_failure.append(route.request.headers['idempotency-key'])
                route.abort('failed')
            else:
                helper_network_failure.append(route.request.headers['idempotency-key'])
                route.fulfill(json=helper_response(route.request))

        page.route('**/api/v1/reports/*/assistant', helper_model)
        page.get_by_role('button', name='Оформление', exact=False).click()
        page.get_by_role('button', name='Разобрать замечание', exact=True).click()
        expect(page.locator('#assistant-answer')).to_contain_text('Локальное объяснение')
        page.locator('#assistant-action').select_option('question')
        page.locator('#assistant-question').fill('Почему это замечание требует проверки?')
        page.locator('#assistant-submit').click()
        expect(page.locator('#assistant-consent')).to_be_focused()
        assert not helper_calls
        page.locator('#assistant-consent').check()
        page.locator('#assistant-submit').click()
        expect(page.locator('#assistant-answer')).to_contain_text('Недостаточно данных')
        assert not page.locator('#assistant-consent').is_checked()
        assert page.locator('#assistant-answer img').count() == 0
        assert page.evaluate('window.__assistant_xss === undefined')
        page.locator('#assistant-action').select_option('rephrase')
        page.locator('#assistant-text').fill('Просим до 24.12.2025 направить 12 экземпляров.')
        page.locator('#assistant-consent').check()
        page.locator('#assistant-submit').click()
        expect(page.get_by_label('Предлагаемая формулировка')).to_have_value('Просим направить 12 экземпляров до 24.12.2025.')
        assert helper_calls[0].headers['idempotency-key'] != helper_calls[1].headers['idempotency-key']
        if screenshots:
            page.screenshot(path=str(screenshots / 'assistant-model.png'), full_page=True)
        page.locator('#assistant-action').select_option('question')
        page.locator('#assistant-question').fill('Как уточнить выбранную просьбу?')
        page.locator('#assistant-consent').check()
        page.locator('#assistant-submit').click()
        expect(page.locator('#assistant-error')).to_contain_text('мог быть принят')
        assert not page.locator('#assistant-consent').is_checked()
        page.locator('#assistant-consent').check()
        page.locator('#assistant-submit').click()
        expect(page.locator('#assistant-answer')).to_contain_text('Ответ модели')
        assert len(helper_network_failure) == 2 and helper_network_failure[0] == helper_network_failure[1]
        page.locator('#close-assistant').click()
        print('OK: assistant consent, insufficient data, rephrasing, XSS text and uncertain retry')

        assert context.cookies() == []
        storage = page.evaluate('({local: {...localStorage}, session: {...sessionStorage}})')
        assert set(storage['local']) <= {'sed-dss-theme'} and storage['session'] == {}
        page.locator('#connection-button').click()
        page.locator('#disconnect-button').click()
        expect(page.locator('#connection-label')).to_have_text('Подключить')
        assert not page.locator('#report-screen').inner_text()
        page.reload()
        expect(page.locator('#connection-label')).to_have_text('Подключить')
        assert page.locator('#api-key').input_value() == ''
        assert not blocked and not errors
        print('OK: no key/report storage, disconnect clears reports, no external browser requests')
        browser.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--screenshots', type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='sed-dss-browser-', ignore_cleanup_errors=True) as temporary:
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        env = dict(os.environ, DATABASE_URL=f'sqlite:///{Path(temporary) / "browser.sqlite3"}',
                   APP_API_KEY=SYNTHETIC_KEY, IDEMPOTENCY_SECRET='synthetic-browser-idempotency',
                   LLM_PROVIDER='none', ALLOW_EXTERNAL_LLM='false', LANGUAGE_TOOL_ENABLED='false')
        process = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'app.main:app',
                                    '--host', '127.0.0.1', '--port', str(port), '--no-access-log',
                                    '--log-level', 'critical'], cwd=ROOT, env=env,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        base_url = f'http://127.0.0.1:{port}'
        try:
            for _ in range(100):
                try:
                    with urllib.request.urlopen(base_url + '/health/ready', timeout=1) as response:
                        if response.status == 200:
                            break
                except (OSError, urllib.error.URLError):
                    if process.poll() is not None:
                        raise RuntimeError('Isolated test server did not start') from None
                    time.sleep(.1)
            else:
                raise RuntimeError('Isolated test server readiness timeout')
            check(base_url, args.screenshots)
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            if sys.platform == 'win32':
                time.sleep(.5)  # Windows may briefly retain a child's SQLite handle.


if __name__ == '__main__':
    main()
