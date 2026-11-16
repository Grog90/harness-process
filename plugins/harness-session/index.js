/**
 * Host-плагин адаптера DSH → harness.
 *
 * - активная задача из `.harness/guard-scope.json` попадает в системный контекст;
 * - инструмент `harness_task` возвращает read-only статус задачи.
 *
 * Плагин не диспетчит, не выносит вердикт и не коммитит.
 */

import { spawnSync } from 'node:child_process';
import { resolveHarnessRoot, readScope, taskContext, buildStatusArgs } from './session-core.mjs';

export const name = 'harness-session';
export const inject = ['tools', 'systemPrompt'];

export function apply(ctx, config) {
  const resolved = resolveHarnessRoot({
    configRoot: config?.root || '',
    envRoot: process.env.HARNESS_ROOT || '',
  });
  const root = resolved.root;
  const disposers = [];

  if (root) {
    disposers.push(ctx.systemPrompt.context({
      name: 'harness-task',
      order: 850,
      text: () => {
        const scope = readScope(root);
        return taskContext(scope) || '';
      },
    }));
  }

  disposers.push(ctx.tools.register({
    name: 'harness_task',
    description: 'Read the active harness task and its last verdict. Read-only; takes no action on the task.',
    parameters: {
      type: 'object',
      properties: {
        task_id: {
          type: 'string',
          description: 'Task id; defaults to the active task in .harness/guard-scope.json.',
        },
      },
      additionalProperties: false,
    },
    output: {
      schema: {
        type: 'object',
        properties: {
          ok: { type: 'boolean' },
          status: { type: 'object' },
          error: { type: 'string' },
        },
        additionalProperties: true,
      },
      render(args, value) {
        return [{ type: 'text', text: JSON.stringify(value, null, 2) }];
      },
    },
    async execute(args) {
      if (!root) return { ok: false, error: 'harness root не найден (config.root/HARNESS_ROOT/~/.dsh/harness-root)' };
      const scope = readScope(root);
      const taskId = String(args?.task_id || scope?.task_id || '');
      if (!taskId) return { ok: false, error: 'нет активной задачи и task_id не передан' };
      const python = config?.python || process.env.HARNESS_PYTHON || 'python';
      const proc = spawnSync(python, buildStatusArgs({ root, taskId }), {
        encoding: 'utf8',
        timeout: 30000,
        windowsHide: true,
      });
      if (proc.error) return { ok: false, error: String(proc.error.message || proc.error) };
      if (proc.status !== 0) {
        const detail = String(proc.stderr || proc.stdout || '').trim().slice(0, 800);
        return { ok: false, error: detail || `harness завершился с кодом ${proc.status}` };
      }
      try {
        return { ok: true, status: JSON.parse(proc.stdout) };
      } catch {
        return { ok: true, raw: String(proc.stdout || '').slice(0, 4000) };
      }
    },
  }));

  return () => {
    for (const dispose of disposers) {
      if (typeof dispose === 'function') dispose();
    }
  };
}
