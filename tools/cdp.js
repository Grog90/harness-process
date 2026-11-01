// cdp.js — выполнить JS в открытом Chrome (CDP 127.0.0.1:9222) и напечатать результат.
// usage: node cdp.js "<expression>"
const http = require('http');

function getJSON(path) {
  return new Promise((resolve, reject) => {
    http.get({ host: '127.0.0.1', port: 9222, path }, (r) => {
      let d = '';
      r.on('data', (c) => (d += c));
      r.on('end', () => {
        try { resolve(JSON.parse(d)); } catch (e) { reject(e); }
      });
    }).on('error', reject);
  });
}

(async () => {
  const expr = process.argv[2];
  if (!expr) { console.error('no expression'); process.exit(2); }
  const targets = await getJSON('/json/list');
  const page = targets.find((t) => t.type === 'page' && /gemini|google/.test(t.url))
    || targets.find((t) => t.type === 'page');
  if (!page) { console.error('no page target'); process.exit(3); }

  const ws = new WebSocket(page.webSocketDebuggerUrl);
  const done = new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('timeout')), 120000);
    ws.onopen = () => {
      ws.send(JSON.stringify({
        id: 1,
        method: 'Runtime.evaluate',
        params: { expression: expr, returnByValue: true, awaitPromise: true, userGesture: true },
      }));
    };
    ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.id !== 1) return;
      clearTimeout(timer);
      if (msg.error) return reject(new Error(JSON.stringify(msg.error)));
      const r = msg.result && msg.result.result;
      if (msg.result && msg.result.exceptionDetails) {
        return reject(new Error(JSON.stringify(msg.result.exceptionDetails)));
      }
      resolve(r && r.value !== undefined ? r.value : r);
    };
    ws.onerror = (e) => reject(new Error('ws error'));
  });

  try {
    const out = await done;
    console.log(typeof out === 'string' ? out : JSON.stringify(out));
  } finally {
    try { ws.close(); } catch (e) {}
  }
})().catch((e) => { console.error('ERR ' + e.message); process.exit(1); });
