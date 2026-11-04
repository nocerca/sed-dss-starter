// Test-only endpoint substitution. The network is internal and has no Internet egress.
import { buildConfig as buildBaseConfig } from './base-config.mjs';
export function buildConfig(env) {
  const config = buildBaseConfig(env);
  config.models.providers.zai.baseUrl = 'http://mock-model:8002/api/paas/v4';
  return config;
}
