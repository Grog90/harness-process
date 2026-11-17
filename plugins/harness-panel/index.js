/**
 * Host half: same-origin прокси к live-доске harness.
 *
 * Клиент в Electron не может ходить напрямую в http://127.0.0.1:8765
 * (CORS/mixed content). Здесь route на том же origin, поэтому панель делает
 * fetch('/harness-panel/metrics.json').
 */
export const inject = ['webServer'];

const BOARD_URL = process.env.HARNESS_BOARD_URL || 'http://127.0.0.1:8765/metrics.json';

export function apply(ctx) {
  const dispose = ctx.webServer.register({
    kind: 'exact',
    path: '/harness-panel/metrics.json',
    async handler(req, res) {
      if (req.method !== 'GET' && req.method !== 'HEAD') {
        res.writeHead(405, { allow: 'GET, HEAD', 'content-type': 'text/plain; charset=utf-8' });
        res.end('method not allowed\n');
        return;
      }
      try {
        const response = await fetch(BOARD_URL, { signal: AbortSignal.timeout(3000) });
        const body = await response.text();
        res.writeHead(response.ok ? 200 : 502, {
          'content-type': 'application/json; charset=utf-8',
          'cache-control': 'no-store',
        });
        res.end(req.method === 'HEAD' ? undefined : body);
      } catch (error) {
        res.writeHead(502, { 'content-type': 'application/json; charset=utf-8' });
        res.end(JSON.stringify({ error: 'board offline', detail: String(error) }));
      }
    },
  });
  ctx.effect(() => dispose);
}
