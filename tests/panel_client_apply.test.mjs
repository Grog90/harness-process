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
console.log('panel client apply ok');
