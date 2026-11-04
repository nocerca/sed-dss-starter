/** Pinned OpenClaw 2026.9.3 configuration; no host files or corpus mounts. */
export function buildConfig(env) {
  const token = env.OPENCLAW_GATEWAY_TOKEN;
  if (!token || token.length < 32) {
    throw new Error('Нужен OPENCLAW_GATEWAY_TOKEN длиной не менее 32 символов');
  }
  const backend = env.MODEL_BACKEND || 'yandex';
  if (!['yandex', 'gigachat', 'zai'].includes(backend)) {
    throw new Error('MODEL_BACKEND: допустимы только yandex, gigachat или zai');
  }
  // GigaChat OAuth is handled by the dedicated internal bridge, not by the API.
  const modelId = backend === 'yandex'
    ? `gpt://${env.YANDEX_FOLDER_ID || 'not-configured'}/${env.YANDEX_MODEL || 'aliceai-llm-flash'}`
    : backend === 'zai'
      ? (env.ZAI_MODEL || 'glm-4.7-flash')
      : (env.GIGACHAT_MODEL || 'GigaChat-2');
  if (backend === 'zai' && !['glm-4.7-flash', 'glm-4.5-flash'].includes(modelId)) {
    throw new Error('ZAI_MODEL: допустимы только бесплатные glm-4.7-flash или glm-4.5-flash');
  }
  if (/llama|ollama/i.test(modelId)) {
    throw new Error('Модели Llama и среда Ollama запрещены');
  }
  const provider = backend === 'yandex' ? {
    baseUrl: 'https://ai.api.cloud.yandex.net/v1',
    apiKey: env.YANDEX_API_KEY || 'not-configured',
    headers: { 'x-data-logging-enabled': 'false' },
  } : backend === 'zai' ? {
    // Standard model API, not the paid Coding Plan endpoint.
    baseUrl: 'https://api.z.ai/api/paas/v4',
    apiKey: env.ZAI_API_KEY || 'not-configured',
  } : {
    baseUrl: 'http://model-bridge:8001/v1',
    apiKey: env.MODEL_BRIDGE_API_KEY || 'not-configured',
  };
  return {
    gateway: {
      mode: 'local', bind: 'lan', port: 18789,
      auth: { mode: 'token', token },
      controlUi: { enabled: false },
      http: { endpoints: { chatCompletions: { enabled: true } } },
    },
    logging: { level: 'silent', consoleLevel: 'silent' },
    discovery: { mdns: { mode: 'off' } },
    plugins: { enabled: false },
    browser: { enabled: false },
    commands: {
      native: false, nativeSkills: false, text: false, bash: false,
      config: false, debug: false, restart: false,
    },
    tools: {
      deny: ['*'], exec: { mode: 'deny' }, elevated: { enabled: false },
      agentToAgent: { enabled: false }, sessions: { visibility: 'self' },
      web: { search: { enabled: false }, fetch: { enabled: false } },
    },
    agents: {
      defaults: {
        model: { primary: `${backend}/${modelId}`, fallbacks: [] },
        models: { [`${backend}/${modelId}`]: {
          streaming: false,
          // Plugins are disabled. Do not rely on the bundled Z.AI plugin to
          // translate thinkingDefault:off into the provider-specific payload.
          ...(backend === 'zai' ? { params: {
            extra_body: { thinking: { type: 'disabled' } },
          } } : {}),
        } },
        thinkingDefault: 'off',
        workspace: '/home/node/.openclaw/workspace',
        skipBootstrap: true, contextInjection: 'never',
        startupContext: { enabled: false },
        heartbeat: { every: '0m' },
        compaction: { memoryFlush: { enabled: false } },
        // Free API queues can take longer than the original 45-second limit.
        timeoutSeconds: backend === 'zai' ? 120 : 45,
      },
      entries: { 'sed-dss': { tools: { deny: ['*'] }, skills: [] } },
    },
    session: {
      maintenance: {
        mode: 'enforce', pruneAfter: '1h', maxEntries: 100,
        resetArchiveRetention: '1h', maxDiskBytes: '32mb', highWaterBytes: '24mb',
      },
    },
    models: {
      mode: 'replace',
      catalogRefresh: { enabled: false },
      providers: {
        [backend]: {
          ...provider, api: 'openai-completions',
          models: [{
            id: modelId, name: { yandex: 'Yandex', gigachat: 'GigaChat', zai: 'Z.AI Flash' }[backend],
            reasoning: false, input: ['text'], contextWindow: 32000, maxTokens: 1600,
            compat: {
              supportsStore: false, supportsDeveloperRole: false,
              supportsReasoningEffort: false, supportsUsageInStreaming: false,
              maxTokensField: 'max_tokens',
              supportsTools: false, requiresStringContent: true, strictMessageKeys: true,
            },
          }],
        },
      },
    },
  };
}
