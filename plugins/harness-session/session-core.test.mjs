import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';

import {
  buildStatusArgs,
  readScope,
  resolveHarnessRoot,
  taskContext,
} from './session-core.mjs';

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
  assert.match(text, /src\/a\.py/);
  assert.match(text, /api \(write\)/);
  assert.match(text, /primary_agent: lead/);
});

test('без задачи контекст не добавляется', () => {
  assert.equal(taskContext(null), null);
  assert.equal(taskContext({}), null);
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
