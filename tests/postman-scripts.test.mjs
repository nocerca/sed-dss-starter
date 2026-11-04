import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const collection = JSON.parse(fs.readFileSync(new URL('../postman/SED-DSS.postman_collection.json', import.meta.url), 'utf8'));
const model = collection.item.flatMap(f => f.item).find(i => i.request.body?.formdata?.some(f => f.value === '{{external_consent}}'));
const script = model.event.find(e => e.listen === 'prerequest').script.exec.join('\n');

test('missing or malformed consent fails closed and does not skip the HTTP response', () => {
  for (const consent of [undefined, '', 'false', 'TRUE', true]) {
    const env = new Map([['external_consent', consent]]), local = new Map(), keys = new Map();
    vm.runInNewContext(script, { pm: {
      environment: { get: k => env.get(k), set: (k,v) => env.set(k,v) },
      variables: { set: (k,v) => local.set(k,v), replaceIn: () => 'synthetic-guid-0001' },
      collectionVariables: { set: (k,v) => keys.set(k,v) },
      execution: { skipRequest: () => assert.fail('The user must receive the server rejection') },
    } });
    assert.equal(local.get('external_consent'), 'false');
    assert.equal(env.get('external_consent'), 'false');
    assert.equal(keys.get('llm_idempotency_key'), 'synthetic-guid-0001');
  }
});

test('consent is consumed before sending and cannot survive a transport timeout', () => {
  const env = new Map([['external_consent', 'true']]), local = new Map();
  const pm = {
    environment: { get: k => env.get(k), set: (k,v) => env.set(k,v) },
    variables: { set: (k,v) => local.set(k,v), replaceIn: () => 'synthetic-guid-0001' },
    collectionVariables: { set: () => {} },
  };
  vm.runInNewContext(script, { pm });
  assert.equal(local.get('external_consent'), 'true');
  assert.equal(env.get('external_consent'), 'false');
  // A second Send, even without running response scripts, requires fresh consent.
  vm.runInNewContext(script, { pm });
  assert.equal(local.get('external_consent'), 'false');
});

test('assistant JSON consent remains boolean and is consumed before each Send', () => {
  const requests = collection.item.flatMap(folder => folder.item).filter(item => item.request.body?.mode === 'raw' && JSON.parse(item.request.body.raw).use_llm);
  assert.equal(requests.length, 2);
  for (const item of requests) {
    for (const value of [undefined, 'false', true, 'true']) {
      const environment = new Map([['external_consent', value]]);
      const body = { raw: item.request.body.raw, update(update) { this.raw = update.raw; } };
      const pm = { request: { body }, environment: { get: key => environment.get(key), set: (key, value) => environment.set(key, value) },
        variables: { set() {}, replaceIn: () => 'synthetic-assistant-guid' }, collectionVariables: { set() {} } };
      const before = item.event.find(event => event.listen === 'prerequest').script.exec.join('\n');
      vm.runInNewContext(before, { pm });
      assert.equal(JSON.parse(body.raw).allow_external_transfer, value === 'true');
      assert.equal(environment.get('external_consent'), 'false');
      vm.runInNewContext(before, { pm });
      assert.equal(JSON.parse(body.raw).allow_external_transfer, false);
    }
  }
});
