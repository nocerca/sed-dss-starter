"""Synthetic finding assistance, consent, historical basis and durable retries."""

import asyncio
import json
from datetime import datetime, timezone
from uuid import uuid4

import httpx
import pytest
from test_api import document_file

from app.assistant import validate_model_answer
from app.assistant_schemas import AssistantRequest
from app.config import get_settings
from app.db import SessionLocal
from app.models import AnalysisRun, AssistantOperation, RuleVersion
from app.retention import purge_reports


def report(client, headers):
    return client.post('/api/v1/analyze', headers=headers,
                       files={'file': document_file()}).json()


def send(client, headers, report_id, **payload):
    return client.post(f'/api/v1/reports/{report_id}/assistant', headers=headers,
                       json={'finding_index': 0, **payload})


def model_output(**overrides):
    return {'status': 'completed', 'answer': 'Уточните согласованный срок в выбранной просьбе.',
            'steps': [], 'suggestion': None, 'source_refs': [], **overrides}


def enable_model(monkeypatch, handler):
    settings = get_settings()
    monkeypatch.setattr(settings, 'allow_external_llm', True)
    monkeypatch.setattr(settings, 'llm_provider', 'openclaw')
    monkeypatch.setattr(settings, 'openclaw_api_key', 'synthetic-assistant-gateway')
    monkeypatch.setattr(settings, 'openclaw_model', 'openclaw/sed-dss')
    monkeypatch.setattr(settings, 'openclaw_base_url', 'http://openclaw:18789/v1')
    client_class = httpx.AsyncClient
    monkeypatch.setattr('app.providers.httpx.AsyncClient', lambda **kwargs: client_class(
        **kwargs, transport=httpx.MockTransport(handler)))


def completion(output):
    return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {
        'content': json.dumps(output, ensure_ascii=False)}}]})


def test_local_help_without_model_and_no_report_mutation(client, headers, monkeypatch):
    original = report(client, headers)

    async def forbidden(*args, **kwargs):
        pytest.fail('Local help must not create a model request')

    monkeypatch.setattr('app.assistant.gateway_completion', forbidden)
    explained = send(client, headers, original['id'])
    assert explained.status_code == 200
    body = explained.json()
    assert body['method'] == 'local' and body['provider'] == 'none'
    assert body['sources'][0]['version'] == original['findings'][0]['rule_version']
    assert body['sources'][0]['verified'] is False
    assert body['sources'][0]['kind'] == 'heuristic'
    fixed = send(client, headers, original['id'], action='fix')
    assert fixed.status_code == 200 and fixed.json()['steps']
    assert client.get(f'/api/v1/reports/{original["id"]}', headers=headers).json() == original
    with SessionLocal() as db:
        assert not db.query(AssistantOperation).filter_by(report_id=original['id']).count()


def test_swagger_request_examples_are_valid_and_never_imply_model_consent(client):
    operation = client.get('/openapi.json').json()['paths']['/api/v1/reports/{report_id}/assistant']['post']
    examples = operation['requestBody']['content']['application/json']['examples']
    assert set(examples) == {'local_explain', 'local_fix', 'question', 'rephrase'}
    for example in examples.values():
        payload = AssistantRequest.model_validate(example['value'])
        assert payload.allow_external_transfer is False


def test_historical_specification_used_not_latest_registry(client, headers):
    original = report(client, headers)
    finding = original['findings'][0]
    with SessionLocal() as db:
        old = db.get(RuleVersion, (finding['code'], finding['rule_version']))
        specification = {**old.specification, 'version': '0.0.1', 'description': 'Синтетическое историческое правило для срока'}
        db.add(RuleVersion(code=finding['code'], version='0.0.1', specification=specification))
        row = db.get(AnalysisRun, original['id'])
        row.findings = [{**finding, 'rule_version': '0.0.1'}]
        db.commit()
    result = send(client, headers, original['id']).json()
    assert result['sources'][0]['version'] == '0.0.1'
    assert result['sources'][0]['requirement'] == 'Синтетическое историческое правило для срока'


@pytest.mark.parametrize('action', ['explain', 'fix'])
def test_missing_basis_does_not_invent_instruction(client, headers, action):
    original = report(client, headers)
    with SessionLocal() as db:
        row = db.get(AnalysisRun, original['id'])
        row.findings = [{**original['findings'][0], 'rule_version': 'missing-synthetic-version'}]
        db.commit()
    result = send(client, headers, original['id'], action=action).json()
    assert result['sources'] == [] and any('недоступна' in warning for warning in result['warnings'])


@pytest.mark.parametrize('payload', [
    {'finding_index': -1}, {'finding_index': 5001}, {'action': 'execute'},
    {'action': 'question'}, {'action': 'question', 'question': '   '},
    {'action': 'question', 'question': 'x' * 1001}, {'action': 'rephrase'},
    {'action': 'rephrase', 'selected_text': 'x' * 2001},
    {'action': 'explain', 'question': 'Неверное поле'},
    {'action': 'fix', 'selected_text': 'Неверное поле'},
    {'use_llm': 'true'}, {'untrusted_history': ['not accepted']},
])
def test_invalid_requests_fail_without_model(client, headers, payload, monkeypatch):
    original = report(client, headers)
    async def forbidden(*args, **kwargs):
        pytest.fail('Invalid request must not reach OpenClaw')
    monkeypatch.setattr('app.assistant.gateway_completion', forbidden)
    assert send(client, headers, original['id'], **payload).status_code == 422


def test_auth_indices_missing_and_processing_reports(client, headers):
    original = report(client, headers)
    assert send(client, {}, original['id']).status_code == 401
    assert send(client, headers, str(uuid4())).status_code == 404
    assert send(client, headers, original['id'], finding_index=100).status_code == 404
    with SessionLocal() as db:
        db.get(AnalysisRun, original['id']).status = 'processing'
        db.commit()
    assert send(client, headers, original['id']).status_code == 409


@pytest.mark.parametrize('global_consent,request_consent', [(False, False), (False, True), (True, False)])
def test_model_needs_both_consents_even_on_replay(client, headers, monkeypatch, global_consent, request_consent):
    original = report(client, headers)
    calls = []
    enable_model(monkeypatch, lambda request: (calls.append(request), completion(model_output()))[1])
    auth = {**headers, 'Idempotency-Key': uuid4().hex}
    data = {'action': 'question', 'question': 'Как уточнить срок?', 'use_llm': True, 'allow_external_transfer': True}
    assert send(client, auth, original['id'], **data).status_code == 200
    monkeypatch.setattr(get_settings(), 'allow_external_llm', global_consent)
    data['allow_external_transfer'] = request_consent
    assert send(client, auth, original['id'], **data).status_code == 409
    assert len(calls) == 1


def test_model_requires_operation_key_and_explicit_mode(client, headers, monkeypatch):
    original = report(client, headers)
    enable_model(monkeypatch, lambda request: pytest.fail('Must fail before network'))
    assert send(client, headers, original['id'], action='question', question='Как уточнить срок?').status_code == 409
    assert send(client, headers, original['id'], use_llm=True, allow_external_transfer=True).status_code == 409


def test_gateway_context_excludes_report_metadata_and_isolated_sessions(client, headers, monkeypatch):
    original = report(client, headers)
    with SessionLocal() as db:
        row = db.get(AnalysisRun, original['id'])
        row.filename = 'SYNTHETIC_PRIVATE_FILENAME.docx'
        row.external_document_id = 'SYNTHETIC_PRIVATE_SED_ID'
        db.commit()
    requests = []
    enable_model(monkeypatch, lambda request: (requests.append(request), completion(model_output()))[1])
    data = {'action': 'question', 'question': 'Как уточнить срок?', 'use_llm': True, 'allow_external_transfer': True}
    auth = {**headers, 'Idempotency-Key': uuid4().hex}
    first = send(client, auth, original['id'], **data)
    replayed = send(client, auth, original['id'], **data)
    assert replayed.status_code == 200 and replayed.json() == first.json()
    assert replayed.headers['Idempotency-Replayed'] == 'true'
    assert replayed.headers['Cache-Control'] == 'no-store'
    assert len(requests) == 1
    assert send(client, {**headers, 'Idempotency-Key': uuid4().hex}, original['id'], **data).status_code == 200
    assert requests[0].headers['x-openclaw-session-key'] != requests[1].headers['x-openclaw-session-key']
    wire = requests[0].content.decode()
    for private in [original['id'], original['request_id'], 'SYNTHETIC_PRIVATE_FILENAME', 'SYNTHETIC_PRIVATE_SED_ID']:
        assert private not in wire
    body = json.loads(wire)
    assert 'tools' not in body and body['model'] == 'openclaw/sed-dss'
    context = json.loads(body['messages'][1]['content'])
    assert context['available_context']['full_letter'] is False
    assert context['untrusted_finding']['code'] == original['findings'][0]['code']
    assert send(client, auth, original['id'], **{**data, 'question': 'Другой вопрос?'}).status_code == 409
    assert len(requests) == 2


@pytest.mark.parametrize('output', [
    model_output(source_refs=['INVENTED-CLAUSE']), model_output(suggestion='Незапрошенная замена'),
    model_output(status='insufficient_data', suggestion='Неверная замена'),
    {'answer': 'missing status'}, model_output(answer='x' * 4001),
])
def test_invalid_model_answers_preserve_original_report_and_consume_key(client, headers, monkeypatch, output):
    original = report(client, headers)
    calls = []
    enable_model(monkeypatch, lambda request: (calls.append(request), completion(output))[1])
    auth = {**headers, 'Idempotency-Key': uuid4().hex}
    for _ in range(2):
        response = send(client, auth, original['id'], use_llm=True, allow_external_transfer=True)
        assert response.status_code == 502
    assert len(calls) == 1
    assert client.get(f'/api/v1/reports/{original["id"]}', headers=headers).json() == original


def test_upstream_errors_do_not_leak_or_automatically_retry(client, headers, monkeypatch):
    original = report(client, headers)
    calls = []
    enable_model(monkeypatch, lambda request: (calls.append(request), httpx.Response(503, text='PRIVATE_PROVIDER_BODY'))[1])
    auth = {**headers, 'Idempotency-Key': uuid4().hex}
    for _ in range(2):
        response = send(client, auth, original['id'], use_llm=True, allow_external_transfer=True)
        assert response.status_code == 502 and 'PRIVATE_PROVIDER_BODY' not in response.text
    assert len(calls) == 1


def test_rephrase_preserves_numeric_facts_and_supports_insufficient_data():
    payload = AssistantRequest(finding_index=0, action='rephrase', use_llm=True,
        selected_text='Просим направить 12 экземпляров до 24.12.2025 на office@example.invalid.')
    good = model_output(suggestion='До 24.12.2025 просим направить 12 экземпляров на office@example.invalid.')
    assert validate_model_answer(json.dumps(good), payload, None).suggestion
    for wrong in [good['suggestion'].replace('12 экземпляров', '15 экземпляров'),
                  good['suggestion'].replace('office@example.invalid', 'other@example.invalid')]:
        from app.providers import ProviderError
        with pytest.raises(ProviderError):
            validate_model_answer(json.dumps(model_output(suggestion=wrong)), payload, None)
    assert validate_model_answer(json.dumps(model_output(status='insufficient_data')), payload, None).suggestion is None


def test_rephrase_accepts_date_moved_to_end_of_sentence_but_not_changed_date():
    from app.providers import ProviderError
    payload = AssistantRequest(finding_index=0, action='rephrase', use_llm=True,
        selected_text='Просим до 24.12.2025 направить 12 экземпляров учебных материалов.')
    suggestion = 'Просим направить 12 экземпляров учебных материалов до 24.12.2025.'
    assert validate_model_answer(json.dumps(model_output(suggestion=suggestion)), payload, None).suggestion == suggestion
    with pytest.raises(ProviderError):
        validate_model_answer(json.dumps(model_output(suggestion=suggestion.replace('24.12.2025', '25.12.2025'))), payload, None)


def test_rephrase_context_uses_only_selected_text_not_old_finding_quote(client, headers, monkeypatch):
    original = report(client, headers)
    requests = []
    suggestion = 'Просим направить 12 экземпляров до 24.12.2025.'
    enable_model(monkeypatch, lambda request: (requests.append(request), completion(model_output(
        answer='Текст не изменился.', suggestion=suggestion)))[1])
    response = send(client, {**headers, 'Idempotency-Key': uuid4().hex}, original['id'],
        action='rephrase', selected_text='Просим до 24.12.2025 направить 12 экземпляров.',
        use_llm=True, allow_external_transfer=True)
    assert response.status_code == 200 and response.json()['suggestion'] == suggestion
    assert 'предложила вариант' in response.json()['answer']
    assert 'Текст не изменился' not in response.json()['answer']
    wire = json.loads(requests[0].content)
    context = json.loads(wire['messages'][1]['content'])
    assert context['selected_text'] == 'Просим до 24.12.2025 направить 12 экземпляров.'
    assert set(context['untrusted_finding']) == {'code', 'category'}
    assert context['untrusted_basis'] == [] and response.json()['sources'] == []


def test_rephrase_identical_text_is_reported_by_literal_comparison(client, headers, monkeypatch):
    original = report(client, headers)
    selected = 'Просим направить учебные материалы.'
    enable_model(monkeypatch, lambda request: completion(model_output(
        answer='Я полностью переписал текст.', suggestion=selected)))
    response = send(client, {**headers, 'Idempotency-Key': uuid4().hex}, original['id'],
        action='rephrase', selected_text=selected, use_llm=True, allow_external_transfer=True)
    assert response.status_code == 200 and 'без изменений' in response.json()['answer']


def test_saved_answer_is_erased_on_report_delete_and_retention(client, headers, monkeypatch):
    calls = []
    enable_model(monkeypatch, lambda request: (calls.append(request), completion(model_output()))[1])
    data = {'action': 'question', 'question': 'Синтетический приватный вопрос?', 'use_llm': True,
            'allow_external_transfer': True, 'selected_text': 'Синтетический выбранный контекст.'}
    for delete_via_api in [True, False]:
        original = report(client, headers)
        auth = {**headers, 'Idempotency-Key': uuid4().hex}
        assert send(client, auth, original['id'], **data).status_code == 200
        with SessionLocal() as db:
            record = db.query(AssistantOperation).filter_by(report_id=original['id']).one()
            assert data['question'] not in json.dumps(record.result, ensure_ascii=False)
            assert data['selected_text'] not in json.dumps(record.result, ensure_ascii=False)
            key_hash = record.key_hash
        if delete_via_api:
            assert client.delete(f'/api/v1/reports/{original["id"]}', headers=headers).status_code == 204
        else:
            with SessionLocal() as db:
                db.get(AnalysisRun, original['id']).created_at = datetime(2000, 1, 1)
                db.commit()
                purge_reports(db, datetime(2001, 1, 1, tzinfo=timezone.utc), execute=True)
        assert send(client, auth, original['id'], **data).status_code == 410
        with SessionLocal() as db:
            row = db.get(AssistantOperation, key_hash)
            assert row.result is None and row.report_id is None
    assert len(calls) == 2


def test_concurrent_operation_and_deletion_while_model_waits(client, headers, monkeypatch):
    from app.main import app
    original = report(client, headers)
    monkeypatch.setattr(get_settings(), 'allow_external_llm', True)
    monkeypatch.setattr(get_settings(), 'llm_provider', 'openclaw')

    async def scenario():
        started, release = asyncio.Event(), asyncio.Event()
        calls = []
        async def waiting(*args, **kwargs):
            calls.append(True)
            started.set()
            await release.wait()
            return json.dumps(model_output())
        monkeypatch.setattr('app.assistant.gateway_completion', waiting)
        auth = {**headers, 'Idempotency-Key': uuid4().hex}
        data = {'finding_index': 0, 'use_llm': True, 'allow_external_transfer': True}
        path = f'/api/v1/reports/{original["id"]}/assistant'
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as browser:
            first = asyncio.create_task(browser.post(path, headers=auth, json=data))
            await started.wait()
            repeated = await browser.post(path, headers=auth, json=data)
            assert repeated.status_code == 409
            assert (await browser.delete(f'/api/v1/reports/{original["id"]}', headers=headers)).status_code == 204
            release.set()
            assert (await first).status_code == 410
            assert (await browser.post(path, headers=auth, json=data)).status_code == 410
        assert len(calls) == 1
    asyncio.run(scenario())
