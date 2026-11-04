"""Smoke-test the ten generated letters through the local REST API without an LLM."""

import argparse
import json
import sys
from pathlib import Path
from uuid import uuid4

import httpx

if __package__:
    from .smoke_test import local_api_key
else:
    from smoke_test import local_api_key

ROOT = Path(__file__).resolve().parent.parent


def check(base_url: str) -> list[dict]:
    directory = ROOT / 'examples' / 'private' / 'postman_cases'
    manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))
    results = []
    with httpx.Client(base_url=base_url, timeout=30, trust_env=False,
                      headers={'X-API-Key': local_api_key(ROOT)}) as client:
        assert client.get('/health/ready').json()['status'] == 'ready'
        cases = [(directory / 'incorrect' / case['filename'], case['expected_codes']) for case in manifest['cases']]
        cases += [(directory / 'controls' / name, []) for name in manifest['controls']]
        for path, expected in cases:
            response = client.post('/api/v1/analyze',
                headers={'Idempotency-Key': uuid4().hex},
                data={'policy_profile': 'office_instruction_letters', 'use_llm': 'false',
                      'allow_external_transfer': 'false', 'use_language_checks': 'false'},
                files={'file': (path.name, path.read_bytes(), 'application/vnd.openxmlformats-officedocument.wordprocessingml.document')})
            assert response.status_code == 200, 'Expected HTTP 200'
            report = response.json()
            actual = sorted(f['code'] for f in report['findings'] if f['code'].startswith('ORG_'))
            assert set(actual) == set(expected), 'Unexpected instruction findings on a synthetic document'
            assert report['status'] == 'completed' and report['provider'] == 'none'
            assert report['metrics']['policy_checks']['profile'] == 'office_instruction_letters'
            saved = client.get('/api/v1/reports/' + report['id']).json()
            assert saved['findings'] == report['findings'], 'Report was not preserved'
            results.append({'filename': path.name, 'expected_codes': expected, 'actual_codes': actual,
                            'report_id': report['id'], 'status': report['status'], 'provider': report['provider']})
    (directory / 'rest_validation.json').write_text(
        json.dumps(results, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return results


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Проверить 10 дефектных DOCX и 2 контроля без модели')
    parser.add_argument('--base-url', default='http://127.0.0.1:8000')
    args = parser.parse_args()
    try:
        results = check(args.base_url)
        print(f'OK: {len(results)} synthetic documents through REST; expected codes matched; no LLM')
    except (OSError, ValueError, KeyError, AssertionError, httpx.HTTPError) as exc:
        print('Demo check failed:', type(exc).__name__, file=sys.stderr)
        sys.exit(1)
