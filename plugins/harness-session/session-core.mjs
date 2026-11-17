/**
 * Чистое ядро адаптера: без Cordis, чтобы проверяться `node --test`.
 */

import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

export function hasPolicy(dir) {
  try {
    return fs.statSync(path.join(dir, 'policies', 'policy.json')).isFile();
  } catch {
    return false;
  }
}

function readPointer(home) {
  try {
    const value = fs.readFileSync(path.join(home, 'harness-root'), 'utf8').trim();
    return value ? path.resolve(value) : null;
  } catch {
    return null;
  }
}

/** Корень harness: config → env → ~/.dsh/harness-root → cwd вверх до политики. */
export function resolveHarnessRoot({
  configRoot = '',
  envRoot = '',
  home = process.env.DSH_HOME || path.join(os.homedir(), '.dsh'),
  cwd = process.cwd(),
} = {}) {
  const candidates = [];
  if (configRoot) candidates.push(path.resolve(configRoot));
  if (envRoot) candidates.push(path.resolve(envRoot));
  const pointer = readPointer(home);
  if (pointer) candidates.push(pointer);
  candidates.push(path.resolve(cwd));
  for (const candidate of candidates) {
    if (hasPolicy(candidate)) return { root: candidate, tried: candidates.slice() };
  }
  let dir = path.resolve(cwd);
  for (let depth = 0; depth < 4; depth += 1) {
    const parent = path.dirname(dir);
    if (parent === dir) break;
    dir = parent;
    if (hasPolicy(dir)) return { root: dir, tried: [...candidates, dir] };
  }
  return { root: null, tried: candidates };
}

export function readScope(root) {
  if (!root) return null;
  try {
    return JSON.parse(fs.readFileSync(path.join(root, '.harness', 'guard-scope.json'), 'utf8'));
  } catch {
    return null;
  }
}

/** Единственное действие инструмента: расширение набора — отдельное решение. */
export const TASK_TOOL_ACTIONS = Object.freeze(['status']);

/** Какой task_id читать; ошибки те же, что у инструмента. */
export function resolveStatusTask({ root, scope, taskId = '' } = {}) {
  if (!root) return { ok: false, error: 'harness root не найден (config.root/HARNESS_ROOT/~/.dsh/harness-root)' };
  const id = String(taskId || scope?.task_id || '');
  if (!id) return { ok: false, error: 'нет активной задачи и task_id не передан' };
  return { ok: true, taskId: id };
}

export const ATTACH_FILE = '.harness/session-attach.json';
export const AUTO_SUBMIT_FILE = '.harness/session-auto-submit.json';

/** Привязка сессии к задаче; null — файла нет или он битый. */
export function readAttach(root) {
  if (!root) return null;
  try {
    const data = JSON.parse(fs.readFileSync(path.join(root, ATTACH_FILE), 'utf8'));
    return data && typeof data === 'object' ? data : null;
  } catch {
    return null;
  }
}

/** Решение по автодиспетчу: dispatch | already | conflict | skip. */
export function dispatchDecision(attach, scope, agentId) {
  if (!attach?.auto_dispatch || !attach.task_id || !agentId) return 'skip';
  if (scope && String(scope.task_id) !== String(attach.task_id)) return 'conflict';
  if (scope && String(scope.task_id) === String(attach.task_id)) return 'already';
  return 'dispatch';
}

/** Одноразовость: после успешного dispatch привязка больше не срабатывает. */
export function consumeAttach(root) {
  try {
    fs.rmSync(path.join(root, '.harness', 'session-attach.json'), { force: true });
    return true;
  } catch {
    return false;
  }
}

/** Opt-in авто-submit: маркер переживает dispatch и снимается после turn-stopping. */
export function readAutoSubmit(root) {
  try {
    const data = JSON.parse(fs.readFileSync(path.join(root, AUTO_SUBMIT_FILE), 'utf8'));
    return data && data.task_id ? data : null;
  } catch {
    return null;
  }
}

export function consumeAutoSubmit(root) {
  try {
    fs.rmSync(path.join(root, AUTO_SUBMIT_FILE), { force: true });
    return true;
  } catch {
    return false;
  }
}

export function autoSubmitArgs({ root, taskId, attempt }) {
  if (!root) throw new Error('root не задан');
  if (!taskId) throw new Error('task_id не задан');
  const n = Number(attempt);
  if (!Number.isInteger(n) || n < 1) throw new Error('attempt не задан');
  return ['-m', 'harness', '--root', root, 'submit', '--task', String(taskId),
    '--attempt', String(n)];
}

/** Ошибка автодиспетча рядом с привязкой; не роняет сессию. */
export function writeAttachError(root, text) {
  if (!root) return null;
  try {
    fs.mkdirSync(path.join(root, '.harness'), { recursive: true });
    const file = path.join(root, '.harness', 'session-attach.error');
    fs.writeFileSync(file, String(text || 'dispatch failed').slice(0, 4000), 'utf8');
    return file;
  } catch {
    return null;
  }
}

/** Аргументы dispatch из привязки. */export function dispatchArgs({ root, taskId, agentId }) {
  if (!root) throw new Error('root не задан');
  if (!taskId) throw new Error('task_id не задан');
  if (!agentId) throw new Error('agent_id не задан');
  return ['-m', 'harness', '--root', root, 'dispatch', '--task', String(taskId),
    '--primary-agent', String(agentId)];
}

/** Текст системного контекста. null — задачи нет, контекст не добавляется. */
export function taskContext(scope) {
  if (!scope || !scope.task_id) return null;
  const lines = [
    '## Harness task',
    `task_id: ${scope.task_id}`,
    `role: ${scope.role || 'worker'}`,
    `phase: ${scope.phase || 'work'}`,
    `attempt: ${scope.attempt ?? '?'}`,
  ];
  const allowed = Array.isArray(scope.allowed_files) ? scope.allowed_files : [];
  lines.push(`allowed_files: ${allowed.length ? allowed.join(', ') : '(none)'}`);
  const services = Array.isArray(scope.services) ? scope.services : [];
  if (services.length) {
    const parts = services.map((s) => {
      const name = String(s.name || '?');
      const mode = s.write ? 'write' : 'read-only';
      return `${name} (${mode})`;
    });
    lines.push(`services: ${parts.join(', ')}`);
  }
  if (scope.primary) lines.push(`primary_agent: ${scope.primary}`);
  return lines.join('\n');
}

/** Аргументы read-only статуса; глобальные флаги — до subcommand. */
export function buildStatusArgs({ root, taskId }) {
  if (!root) throw new Error('root не задан');
  if (!taskId) throw new Error('task_id не задан');
  return ['-m', 'harness', '--root', root, '--json', 'task', 'status', '--task', String(taskId)];
}
