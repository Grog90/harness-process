/**
 * Проверка плагина без установки: поднимаем apply() с поддельным контекстом,
 * забираем зарегистрированный guard и прогоняем вызовы инструментов.
 *
 * Нужна, чтобы «плагин написан» не означало «плагин работает»: здесь видно
 * реальные решения, которые он вернёт рантайму.
 *
 * stdin:  {"root": "...", "calls": [{"name": "write", "arguments": {"path": "src/a.py"}}]}
 * stdout: [{"name": "...", "deny": "R-WORK-1: ..." | null}, ...]
 */

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const root = path.resolve(input.root || '.');
const here = path.dirname(fileURLToPath(import.meta.url));
const pluginPath = path.join(here, 'index.js');

const plugin = await import(pathToFileURL(pluginPath).href);

let captured = null;
const ctx = {
  tools: {
    guard(fn) {
      captured = fn;
      return () => {
        captured = null;
      };
    },
  },
};

plugin.apply(ctx, { root });
if (typeof captured !== 'function') {
  console.error('плагин не зарегистрировал guard');
  process.exit(1);
}

const results = (input.calls || []).map((call) => {
  const reason = captured({ name: call.name, arguments: call.arguments || {} });
  return { name: call.name, args: call.arguments || {}, deny: reason ?? null };
});

process.stdout.write(JSON.stringify(results));
