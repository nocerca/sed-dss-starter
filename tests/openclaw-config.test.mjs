import test from 'node:test';
import assert from 'node:assert/strict';
import { buildConfig } from '../deploy/openclaw-config.mjs';

const env = { OPENCLAW_GATEWAY_TOKEN: 't'.repeat(40) };

test('isolated agent and no tool/command/browser access', () => {
  const config = buildConfig(env);
  assert.deepEqual(config.tools.deny, ['*']);
  assert.deepEqual(config.agents.entries['sed-dss'].tools.deny, ['*']);
  assert.equal(config.tools.exec.mode, 'deny');
  assert.equal(config.tools.elevated.enabled, false);
  assert.equal(config.plugins.enabled, false);
  assert.equal(config.browser.enabled, false);
  assert.equal(config.commands.text, false);
  assert.equal(config.gateway.controlUi.enabled, false);
});

test('gateway requires authentication and no background model calls', () => {
  const config = buildConfig(env);
  assert.equal(config.gateway.auth.mode, 'token');
  assert.equal(config.gateway.http.endpoints.chatCompletions.enabled, true);
  assert.equal(config.agents.defaults.heartbeat.every, '0m');
  assert.equal(config.agents.defaults.compaction.memoryFlush.enabled, false);
  assert.deepEqual(config.agents.defaults.model.fallbacks, []);
  assert.equal(config.logging.level, 'silent');
});

test('missing or weak token prevents gateway launch', () => {
  for (const token of ['', 'short']) {
    assert.throws(() => buildConfig({ OPENCLAW_GATEWAY_TOKEN: token }));
  }
});

test('Yandex model URI, API key and data logging opt-out', () => {
  const config = buildConfig({ ...env, YANDEX_FOLDER_ID: 'synthetic-folder', YANDEX_API_KEY: 'test-key' });
  const provider = config.models.providers.yandex;
  assert.equal(provider.baseUrl, 'https://ai.api.cloud.yandex.net/v1');
  assert.equal(provider.models[0].id, 'gpt://synthetic-folder/aliceai-llm-flash');
  assert.equal(provider.headers['x-data-logging-enabled'], 'false');
  assert.equal(provider.apiKey, 'test-key');
});

test('GigaChat uses internal bridge with no direct model credentials', () => {
  const config = buildConfig({ ...env, MODEL_BACKEND: 'gigachat', MODEL_BRIDGE_API_KEY: 'test-key' });
  assert.equal(config.models.providers.gigachat.baseUrl, 'http://model-bridge:8001/v1');
  assert.deepEqual(Object.keys(config.models.providers), ['gigachat']);
  assert.equal(config.agents.defaults.models['gigachat/GigaChat-2'].streaming, false);
});

test('Z.AI uses the standard endpoint and only the chosen free model', () => {
  const config = buildConfig({ ...env, MODEL_BACKEND: 'zai', ZAI_API_KEY: 'synthetic-zai-key' });
  const provider = config.models.providers.zai;
  assert.equal(provider.baseUrl, 'https://api.z.ai/api/paas/v4');
  assert.equal(provider.apiKey, 'synthetic-zai-key');
  assert.equal(provider.models[0].id, 'glm-4.7-flash');
  assert.equal(config.agents.defaults.model.primary, 'zai/glm-4.7-flash');
  assert.deepEqual(config.agents.defaults.model.fallbacks, []);
  assert.deepEqual(Object.keys(config.models.providers), ['zai']);
  assert.equal(config.agents.defaults.thinkingDefault, 'off');
  assert.equal(config.agents.defaults.timeoutSeconds, 120);
  assert.deepEqual(config.agents.defaults.models['zai/glm-4.7-flash'].params.extra_body,
    { thinking: { type: 'disabled' } });
  assert.deepEqual(config.tools.deny, ['*']);
});

test('Z.AI allows the older free Flash but rejects paid or unrelated models', () => {
  const config = buildConfig({ ...env, MODEL_BACKEND: 'zai', ZAI_MODEL: 'glm-4.5-flash' });
  assert.equal(config.models.providers.zai.models[0].id, 'glm-4.5-flash');
  for (const model of ['glm-4.7', 'glm-4.7-flashx', 'glm-5.3', 'llama', 'openrouter/free']) {
    assert.throws(() => buildConfig({ ...env, MODEL_BACKEND: 'zai', ZAI_MODEL: model }));
  }
});

test('Z.AI without a key can be configured for rule-only operation', () => {
  const config = buildConfig({ ...env, MODEL_BACKEND: 'zai' });
  assert.equal(config.models.providers.zai.apiKey, 'not-configured');
  assert.deepEqual(config.agents.defaults.model.fallbacks, []);
});

test('only approved backends and model families', () => {
  for (const backend of ['ollama', 'openrouter', 'generic']) {
    assert.throws(() => buildConfig({ ...env, MODEL_BACKEND: backend }));
  }
  assert.throws(() => buildConfig({ ...env, YANDEX_MODEL: 'llama-example' }));
});
