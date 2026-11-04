import asyncio
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import delete
from test_api import document_file

from app.analysis_cache import AnalysisCache
from app.config import get_settings
from app.db import SessionLocal
from app.language import LanguageResult
from app.models import AnalysisRun, KnowledgeDocument, RuleDefinition
from app.providers import ProviderError


def test_cache_ttl_capacity_and_busy_state_never_retain_document_content():
    now = [10.0]
    cache = AnalysisCache(10, 2, clock=lambda: now[0])
    key = cache.fingerprint(b'PRIVATE_DOCUMENT_BYTES', {'key': 'PRIVATE_CONFIGURATION'})
    assert cache.claim(key).status == 'miss'
    assert cache.claim(key).status == 'busy'
    cache.finish(key, 'report-a')
    assert cache.claim(key).report_id == 'report-a'
    for name in ['key-b', 'key-c']:
        assert cache.claim(name).status == 'miss'
        cache.finish(name, name)
    assert cache.claim(key).status == 'miss'  # LRU evicted oldest, even before TTL.
    cache.finish(key, None)
    now[0] = 21
    assert cache.claim('key-c').status == 'miss'
    cache.finish('key-c', None)
    assert 'PRIVATE_' not in repr(cache._entries) + repr(cache._pending)
    assert key != AnalysisCache().fingerprint(b'PRIVATE_DOCUMENT_BYTES', {'key': 'PRIVATE_CONFIGURATION'})


def test_pending_capacity_and_failed_owner_are_released():
    cache = AnalysisCache(max_entries=1)
    assert cache.claim('a').status == 'miss'
    assert cache.claim('b').status == 'bypass'
    cache.finish('a', None)
    assert cache.claim('a').status == 'miss'
    cache.finish('a', 'report-a')
    cache.invalidate_report('report-a')
    assert cache.claim('a').status == 'miss'
    cache.clear()
    assert not cache._pending and not cache._entries
    with pytest.raises(ValueError):
        AnalysisCache(ttl_seconds=0)


def enable_model(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, 'llm_provider', 'openclaw')
    monkeypatch.setattr(settings, 'allow_external_llm', True)
    calls = []
    async def model(*args, **kwargs):
        calls.append(1)
        return []
    monkeypatch.setattr('app.main.analyze_with_llm', model)
    return calls


def send(client, headers, file, **form):
    return client.post('/api/v1/analyze', files={'file': file}, headers=headers,
        data={'use_llm': True, 'allow_external_transfer': True, **form})


def test_fresh_keys_reuse_completed_report_and_keep_idempotency_tombstones(client, headers, monkeypatch):
    calls = enable_model(monkeypatch)
    file = document_file()
    first_key, second_key = uuid4().hex, uuid4().hex
    first = send(client, {**headers, 'Idempotency-Key': first_key}, file)
    second = send(client, {**headers, 'Idempotency-Key': second_key}, file)
    assert first.headers['Analysis-Cache'] == 'miss'
    assert second.headers['Analysis-Cache'] == 'hit'
    assert first.json()['id'] == second.json()['id'] and len(calls) == 1
    assert first.json()['metrics'] == second.json()['metrics']
    replay = send(client, {**headers, 'Idempotency-Key': second_key}, file)
    assert replay.headers['Idempotency-Replayed'] == 'true'
    assert replay.headers['Analysis-Cache'] == 'bypass'
    assert second.headers['Cache-Control'] == 'no-store'
    report_id = first.json()['id']
    assert client.delete('/api/v1/reports/' + report_id, headers=headers).status_code == 204
    assert send(client, {**headers, 'Idempotency-Key': second_key}, file).status_code == 410
    third = send(client, headers, file)
    assert third.headers['Analysis-Cache'] == 'miss' and len(calls) == 2
    assert third.json()['id'] != report_id


def test_cache_never_bypasses_authentication_or_external_consent(client, headers, monkeypatch):
    calls = enable_model(monkeypatch)
    file = document_file()
    assert send(client, headers, file).status_code == 200
    assert send(client, {}, file).status_code == 401
    assert send(client, headers, file, allow_external_transfer=False).status_code == 409
    monkeypatch.setattr(get_settings(), 'allow_external_llm', False)
    assert send(client, headers, file).status_code == 409
    assert len(calls) == 1


def test_changes_to_bytes_metadata_profile_model_and_rule_state_invalidate(client, headers, monkeypatch):
    calls = enable_model(monkeypatch)
    file = document_file()
    baseline = send(client, headers, file).json()['id']
    for changed, form in [(document_file('Другой текст'), {}),
                          (("renamed.docx", file[1], file[2]), {}),
                          (file, {'external_document_id': 'OTHER-LOCAL-ID'}),
                          (file, {'document_type': 'response'}),
                          (file, {'template_id': 'other-template'}),
                          (file, {'policy_profile': 'office_instruction_letters'})]:
        response = send(client, headers, changed, **form)
        assert response.status_code == 200 and response.headers['Analysis-Cache'] == 'miss'
        assert response.json()['id'] != baseline
    monkeypatch.setattr(get_settings(), 'zai_model', 'different-test-model')
    assert send(client, headers, file).headers['Analysis-Cache'] == 'miss'
    with SessionLocal() as db:
        rule = db.get(RuleDefinition, 'CONTENT_VAGUE_DEADLINE')
        rule.enabled = 0
        db.commit()
    try:
        assert send(client, headers, file).headers['Analysis-Cache'] == 'miss'
    finally:
        with SessionLocal() as db:
            db.get(RuleDefinition, 'CONTENT_VAGUE_DEADLINE').enabled = 1
            db.commit()
    assert len(calls) == 9


def test_corpus_content_and_source_metadata_changes_invalidate(client, headers, monkeypatch):
    calls = enable_model(monkeypatch)
    file = document_file()
    send(client, headers, file)
    with SessionLocal() as db:
        item = KnowledgeDocument(filename='synthetic.txt', kind='good', source_ref='synthetic', content='Учебные материалы')
        db.add(item)
        db.commit()
        item_id = item.id
    try:
        assert send(client, headers, file).headers['Analysis-Cache'] == 'miss'
        with SessionLocal() as db:
            db.get(KnowledgeDocument, item_id).source_ref = 'changed-source'
            db.commit()
        assert send(client, headers, file).headers['Analysis-Cache'] == 'miss'
        with SessionLocal() as db:
            db.get(KnowledgeDocument, item_id).content = 'Изменённые учебные материалы'
            db.commit()
        assert send(client, headers, file).headers['Analysis-Cache'] == 'miss'
    finally:
        with SessionLocal() as db:
            db.delete(db.get(KnowledgeDocument, item_id))
            db.commit()
    assert len(calls) == 4


def test_failures_and_bypassed_runs_are_not_cached(client, headers, monkeypatch):
    calls = enable_model(monkeypatch)
    file = document_file()
    async def failed(*args, **kwargs):
        raise ProviderError('Synthetic provider failure')
    monkeypatch.setattr('app.main.analyze_with_llm', failed)
    for _ in range(2):
        assert send(client, headers, ('broken.docx', b'not-a-docx', file[2])).status_code == 422
    for _ in range(2):
        response = send(client, headers, file)
        assert response.json()['status'] == 'partial' and response.headers['Analysis-Cache'] == 'miss'
    async def model(*args, **kwargs):
        calls.append(1)
        return []
    monkeypatch.setattr('app.main.analyze_with_llm', model)
    for _ in range(2):
        assert send(client, headers, file, use_cache=False).headers['Analysis-Cache'] == 'bypass'
    assert send(client, headers, file).headers['Analysis-Cache'] == 'miss'
    assert len(calls) == 3
    monkeypatch.setattr(get_settings(), 'analysis_cache_enabled', False)
    assert send(client, headers, file).headers['Analysis-Cache'] == 'bypass'
    assert len(calls) == 4


def test_local_language_reuse_and_fast_rule_only_bypass(client, headers, monkeypatch):
    calls = []
    async def language(settings, document):
        calls.append(1)
        return LanguageResult([], len(document.text), False, '6.6')
    monkeypatch.setattr('app.main.check_language', language)
    file = document_file()
    for expected in ['miss', 'hit']:
        response = client.post('/api/v1/analyze', headers=headers, files={'file': file}, data={'use_language_checks': True})
        assert response.headers['Analysis-Cache'] == expected
        assert response.json()['provider'] == 'none'
    assert len(calls) == 1
    response = client.post('/api/v1/analyze', headers=headers, files={'file': file})
    assert response.headers['Analysis-Cache'] == 'bypass'


def test_deleted_report_from_retention_is_not_resurrected(client, headers, monkeypatch):
    calls = enable_model(monkeypatch)
    file = document_file()
    first = send(client, headers, file).json()
    with SessionLocal() as db:
        db.execute(delete(AnalysisRun).where(AnalysisRun.id == first['id']))
        db.commit()
    second = send(client, headers, file)
    assert second.headers['Analysis-Cache'] == 'miss' and len(calls) == 2


def test_cache_alias_database_failure_does_not_expose_private_sql(client, headers, monkeypatch):
    from sqlalchemy.exc import IntegrityError

    calls = enable_model(monkeypatch)
    file = document_file()
    assert send(client, headers, file).status_code == 200
    def failed(*args, **kwargs):
        raise IntegrityError('PRIVATE_SQL', {'value': 'PRIVATE_PARAMETER'}, Exception('PRIVATE_DATABASE_ERROR'))
    monkeypatch.setattr('app.main.persist_run', failed)
    response = send(client, {**headers, 'Idempotency-Key': uuid4().hex}, file)
    assert response.status_code == 503 and 'PRIVATE_' not in response.text
    assert len(calls) == 1


def test_parallel_different_keys_start_heavy_stage_once(client, headers, monkeypatch):
    import httpx

    from app.main import app

    enable_model(monkeypatch)
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []
        async def slow(*args, **kwargs):
            calls.append(1)
            entered.set()
            await release.wait()
            return []
        monkeypatch.setattr('app.main.analyze_with_llm', slow)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as browser:
            file = document_file()
            data = {'use_llm': True, 'allow_external_transfer': True}
            first = asyncio.create_task(browser.post('/api/v1/analyze', headers={**headers, 'Idempotency-Key': uuid4().hex}, files={'file': file}, data=data))
            await asyncio.wait_for(entered.wait(), 5)
            try:
                second = await browser.post('/api/v1/analyze', headers={**headers, 'Idempotency-Key': uuid4().hex}, files={'file': file}, data=data)
                assert second.status_code == 409 and second.headers['Analysis-Cache'] == 'busy'
            finally:
                release.set()
            report = await first
            third = await browser.post('/api/v1/analyze', headers=headers, files={'file': file}, data=data)
            assert third.headers['Analysis-Cache'] == 'hit' and third.json()['id'] == report.json()['id']
        assert len(calls) == 1
    asyncio.run(scenario())


def test_unexpected_failure_releases_claim_and_use_cache_affects_idempotency(client, headers, monkeypatch):
    calls = enable_model(monkeypatch)
    file = document_file()
    async def broken(*args, **kwargs):
        raise HTTPException(503, 'synthetic preflight failure')
    original = __import__('app.main', fromlist=['perform_analysis']).perform_analysis
    monkeypatch.setattr('app.main.perform_analysis', broken)
    for _ in range(2):
        response = send(client, headers, file)
        assert response.status_code == 503
    monkeypatch.setattr('app.main.perform_analysis', original)
    auth = {**headers, 'Idempotency-Key': uuid4().hex}
    assert send(client, auth, file).status_code == 200
    assert send(client, auth, file, use_cache=False).status_code == 409
    assert len(calls) == 1
