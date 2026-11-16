/**
 * Проверка GAP→ask без установки плагина: поднимаем apply() с контекстом, который
 * умеет `on('tools/pre-execute')`, и прогоняем пишущие/читающие вызовы.
 *
 * stdin:  {"root": "...", "calls": [{"name": "write", "arguments": {...}}]}
 * stdout: [{ "name": "...", "kind": "ask"|"allow", "reason": "..."|null }, ...]
 */

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const root = path.resolve(input.root || '.');
const here = path.dirname(fileURLToPath(import.meta.url));
const plugin = await import(pathToFileURL(path.join(here, 'index.js')).href);

let preExecute = null;
const ctx = {
  tools: {
    guard() {
      return () => {};
    },
  },
  on(event, handler) {
    if (event === 'tools/pre-execute') preExecute = handler;
    return () => {
      preExecute = null;
    };
  },
};

plugin.apply(ctx, { root });
if (typeof preExecute !== 'function') {
  console.error('плагин не зарегистрировал tools/pre-execute');
  process.exit(1);
}

const decisions = [];
for (const call of input.calls || []) {
  const exec = {
    name: call.name,
    arguments: call.arguments || {},
    agent: call.agent ? { id: call.agent } : undefined,
    parent: call.parent ? { id: 'child' } : undefined,
  };
  const decision = await preExecute(exec, async () => ({ kind: 'allow' }));
  decisions.push({
    name: call.name,
    kind: decision?.kind || 'allow',
    reason: decision?.reason || null,
  });
}
process.stdout.write(JSON.stringify(decisions));
