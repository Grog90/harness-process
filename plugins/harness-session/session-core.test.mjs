import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';

import {
  buildStatusArgs,
  readScope,
  resolveHarnessRoot,
  resolveStatusTask,
  taskContext,
} from './session-core.mjs';
import { actions } from './index.js';

function tempRoot() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'harness-session-'));
  fs.mkdirSync(path.join(root, 'policies'), { recursive: true });
  fs.writeFileSync(path.join(root, 'policies', 'policy.json'), '{}');
  fs.mkdirSync(path.join(root, '.harness'), { recursive: true });
  return root;
}

test('resolveHarnessRoot находит корень по политике', () => {
  const root = tempRoot();
  const resolved = resolveHarnessRoot({ cwd: root, home: path.join(root, 'empty-home') });
  assert.equal(resolved.root, path.resolve(root));
});

test('taskContext содержит задачу, роль и allowed_files', () => {
  const text = taskContext({
    task_id: 'T-1',
    role: 'worker',
    phase: 'work',
    attempt: 2,
    allowed_files: ['src/a.py', 'service:api/src/b.py'],
    services: [{ name: 'api', write: true }],
    primary: 'lead',
  });
  assert.match(text, /task_id: T-1/);
  assert.match(text, /role: worker/);
  assert.match(text, /phase: work/);
  assert.match(text, /attempt: 2/);
  assert.match(text, /src\/a\.py/);
  assert.match(text, /api \(write\)/);
  assert.match(text, /primary_agent: lead/);
});

test('без задачи контекст не добавляется и readScope молчит', () => {
  assert.equal(taskContext(null), null);
  assert.equal(taskContext({}), null);
  assert.equal(readScope(tempRoot()), null);
  assert.equal(readScope(null), null);
});

test('инструмент имеет только действие status', () => {
  assert.deepEqual(actions, ['status']);
  assert.deepEqual(resolveStatusTask({ root: '/work', scope: null, taskId: 'T-4' }),
    { ok: true, taskId: 'T-4' });
  assert.equal(resolveStatusTask({ root: '', scope: null }).ok, false);
  assert.equal(resolveStatusTask({ root: '/work', scope: null }).ok, false);
  assert.equal(resolveStatusTask({ root: '/work', scope: { task_id: 'T-5' } }).taskId, 'T-5');
});

test('readScope читает guard-scope.json', () => {
  const root = tempRoot();
  fs.writeFileSync(
    path.join(root, '.harness', 'guard-scope.json'),
    JSON.stringify({ task_id: 'T-2', role: 'verifier' }),
  );
  assert.equal(readScope(root).task_id, 'T-2');
  assert.equal(readScope(root).role, 'verifier');
});

test('buildStatusArgs ставит глобальные флаги до subcommand', () => {
  const args = buildStatusArgs({ root: '/work', taskId: 'T-3' });
  assert.deepEqual(args, ['-m', 'harness', '--root', '/work', '--json', 'task', 'status', '--task', 'T-3']);
  assert.throws(() => buildStatusArgs({ root: '', taskId: 'T-3' }));
});
