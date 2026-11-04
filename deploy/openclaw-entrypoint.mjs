import { mkdirSync, writeFileSync } from 'node:fs';
import { spawn, spawnSync } from 'node:child_process';
import { buildConfig } from './openclaw-config.mjs';

try {
  const config = buildConfig(process.env);
  mkdirSync('/home/node/.openclaw/workspace', { recursive: true });
  writeFileSync('/home/node/.openclaw/openclaw.json', JSON.stringify(config), { mode: 0o600 });
  const validation = spawnSync('node', ['dist/index.js', 'config', 'validate', '--json'], {
    cwd: '/app', encoding: 'utf8', timeout: 60000, maxBuffer: 1024 * 1024,
  });
  if (validation.status !== 0) {
    process.stderr.write('Конфигурация отклонена схемой установленного OpenClaw\n');
    try {
      const result = JSON.parse(validation.stdout);
      // Only schema paths, never rejected values, full diagnostics or secrets.
      for (const issue of result.issues || []) {
        process.stderr.write(`Поле: ${JSON.stringify(issue.path)}\n`);
      }
    } catch { /* CLI output is intentionally not echoed. */ }
    process.exit(1);
  }
  if (process.env.OPENCLAW_VALIDATE_ONLY === '1') {
    process.stdout.write('Конфигурация OpenClaw соответствует схеме\n');
    process.exit(0);
  }
  const child = spawn('node', ['dist/index.js', 'gateway', '--allow-unconfigured'], {
    // The gateway can print agent replies directly even with logging=silent.
    // Never forward its output (which can contain quotes from a letter) to Docker logs.
    cwd: '/app', stdio: 'ignore',
  });
  for (const signal of ['SIGTERM', 'SIGINT']) {
    process.on(signal, () => child.kill(signal));
  }
  child.on('exit', (code) => {
    if (code) process.stderr.write(`OpenClaw завершился с кодом ${code}\n`);
    process.exit(code ?? 1);
  });
  child.on('error', () => {
    process.stderr.write('Не удалось запустить OpenClaw\n');
    process.exit(1);
  });
} catch {
  process.stderr.write('Ошибка конфигурации OpenClaw: проверьте локальные настройки по руководству\n');
  process.exit(1);
}
