import assert from 'node:assert/strict';

process.env.HARNESS_BOARD_URL = 'http://board.test/metrics.json';
const mod = await import(new URL('../plugins/harness-panel/index.js', import.meta.url));

let handler;
let disposed = 0;
const ctx = {
  webServer: {
    register(route) {
      assert.equal(route.kind, 'exact');
      assert.equal(route.path, '/harness-panel/metrics.json');
      handler = route.handler;
      return () => { disposed += 1; };
    },
  },
  effect(fn) { fn(); },
};
mod.apply(ctx);
assert.equal(typeof handler, 'function');

function response() {
  return {
    status: 0,
    headers: {},
    body: '',
    writeHead(status, headers = {}) { this.status = status; this.headers = headers; },
    end(body) { this.body = body ?? ''; },
  };
}

globalThis.fetch = async () => ({ ok: true, text: async () => '{"summary":{"changes":1}}' });
let res = response();
await handler({ method: 'GET' }, res);
assert.equal(res.status, 200);
assert.equal(res.body, '{"summary":{"changes":1}}');

globalThis.fetch = async () => { throw new Error('offline'); };
res = response();
await handler({ method: 'GET' }, res);
assert.equal(res.status, 502);
assert.match(res.body, /board offline/);

res = response();
await handler({ method: 'POST' }, res);
assert.equal(res.status, 405);

globalThis.fetch = async () => ({ ok: true, text: async () => '{}' });
res = response();
await handler({ method: 'HEAD' }, res);
assert.equal(res.status, 200);
assert.equal(res.body, '');

console.log('panel host route ok');
