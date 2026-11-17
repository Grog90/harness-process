/**
 * Host-плагин адаптера DSH → harness.
 *
 * - активная задача из `.harness/guard-scope.json` попадает в системный контекст;
 * - привязка `.harness/session-attach.json` автоматически диспетчит сессию;
 * - инструмент `harness_task` возвращает read-only статус задачи.
 *
 * Вердикт и commit плагин не делает.
 */

import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import {
  TASK_TOOL_ACTIONS,
  buildStatusArgs,
  consumeAttach,
  dispatchDecision,
  dispatchArgs,
  readAttach,
  readScope,
  resolveHarnessRoot,
  resolveStatusTask,
  taskContext,
  writeAttachError,
} from './session-core.mjs';

export const name = 'harness-session';
export const inject = ['tools', 'systemPrompt', 'subagents'];
export const actions = TASK_TOOL_ACTIONS;

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

    // Автодиспетч: привязка есть, задача ещё не в scope — вызываем CLI один раз.
    if (typeof ctx.on === 'function') {
      disposers.push(ctx.on('agent/created', async (payload) => {
        try {
          const attach = readAttach(root);
          const scope = readScope(root);
          const agent = payload?.agent;
          if (!agent?.id) return;
          // Только корневой агент: субагент не должен стать primary задачи.
          let isRoot = !agent.parent;
          try {
            if (typeof ctx.subagents?.rootOf === 'function') {
              isRoot = ctx.subagents.rootOf(agent)?.id === agent.id;
            }
          } catch {
            // fallback: agent.parent
          }
          if (!isRoot) return;
          const decision = dispatchDecision(attach, scope, agent.id);
          if (decision === 'conflict') {
            writeAttachError(root, `active task ${scope.task_id} != attach ${attach.task_id}; detach/clear first`);
            return;
          }
          if (decision !== 'dispatch') return;
          const python = config?.python || process.env.HARNESS_PYTHON || 'python';
          let proc;
          try {
            proc = spawnSync(python, dispatchArgs({
              root, taskId: attach.task_id, agentId: String(agent.id),
            }), { encoding: 'utf8', timeout: 15000, windowsHide: true });
          } catch (error) {
            writeAttachError(root, String(error?.message || error));
            return;
          }
          if (proc.error) {
            writeAttachError(root, String(proc.error.message || proc.error));
            return;
          }
          if (proc.status !== 0) {
            writeAttachError(root, proc.stderr || proc.stdout || `dispatch exit ${proc.status}`);
            return;
          }
          if (!consumeAttach(root)) {
            writeAttachError(root, 'dispatch succeeded but attach could not be consumed');
          }
          try {
            fs.rmSync(path.join(root, '.harness', 'session-attach.error'), { force: true });
          } catch {
            // устаревшая ошибка не должна мешать успешному dispatch
          }
        } catch {
          // Привязка не должна ронять создание агента.
        }
      }));
    }
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
      const scope = readScope(root);
      const resolvedTask = resolveStatusTask({ root, scope, taskId: args?.task_id || '' });
      if (!resolvedTask.ok) return resolvedTask;
      const taskId = resolvedTask.taskId;
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
