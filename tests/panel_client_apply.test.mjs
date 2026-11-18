import assert from 'node:assert/strict';
import fs from 'node:fs';

const src = fs.readFileSync(new URL('../plugins/harness-panel/client.js', import.meta.url), 'utf8');
let plugin;
globalThis.window = { __ModuleLoader__: { load(def) { plugin = def.factory(require); } } };
function require(name) {
  if (name === 'react') {
    return { createElement() {}, useState() {}, useEffect() {} };
  }
  throw new Error('unexpected require ' + name);
}
globalThis.fetch = async () => ({ ok: true, json: async () => ({}) });
// eslint-disable-next-line no-eval
eval(src);

const registered = [];
let selected = null;
const ctx = {
  get(name) { return name === 'layout' ? { selectPanel: (id) => { selected = id; } } : undefined; },
  slots: {
    inject(name, callback) { callback(); },
    register(options, component) {
      assert.equal(typeof component, 'function');
      registered.push(options);
      return () => {};
    },
  },
};
plugin.apply(ctx);
assert.deepEqual(registered.map((r) => [r.name, r.id || r.key]),
  [['conversation.composer.dock', 'harness-panel'], ['main', 'harness-board']]);
assert.ok(plugin.inject.includes('slots'));

const changes = [
  { change_id: 'a-ready', status: 'READY' },
  { change_id: 'a-run', status: 'IN_PROGRESS' },
  { change_id: 'a-block', status: 'VERIFY_BLOCKED' },
  { change_id: 'a-done', status: 'ARCHIVED' },
  { change_id: 'a-cancel', status: 'CANCELLED' },
];
const ids = (rows) => rows.map((r) => r.change_id);
assert.deepEqual(ids(plugin.filterChanges(changes, 'active', '')), ['a-ready', 'a-run', 'a-block']);
assert.deepEqual(ids(plugin.filterChanges(changes, 'ready', '')), ['a-ready']);
assert.deepEqual(ids(plugin.filterChanges(changes, 'blocked', '')), ['a-block']);
assert.deepEqual(ids(plugin.filterChanges(changes, 'archive', '')), ['a-done']);
assert.deepEqual(ids(plugin.filterChanges(changes, 'all', '')), ids(changes));
assert.deepEqual(ids(plugin.filterChanges(changes, 'all', 'CANCEL')), ['a-cancel']);

plugin.openBoard();
assert.equal(selected, 'harness-board');
console.log('panel client apply ok');
