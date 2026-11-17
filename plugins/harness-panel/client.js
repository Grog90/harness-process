window.__ModuleLoader__.load({
  id: '@local/harness-panel',
  factory(require) {
    const React = require('react');
    const h = React.createElement;
    const DEFAULT_URL = '/harness-panel/metrics.json';

    function badge(color) {
      return {
        display: 'inline-block', padding: '0 6px', borderRadius: 9,
        fontSize: 11, lineHeight: '18px', background: color, color: '#fff',
      };
    }

    function HarnessPanel() {
      const [data, setData] = React.useState(null);
      const [error, setError] = React.useState('');
      const [tick, setTick] = React.useState(0);

      React.useEffect(() => {
        let alive = true;
        const url = (typeof localStorage !== 'undefined'
          && localStorage.getItem('harnessBoardUrl')) || DEFAULT_URL;
        const load = () => fetch(url, { cache: 'no-store' })
          .then((r) => {
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
          })
          .then((json) => { if (alive) { setData(json); setError(''); } })
          .catch((err) => { if (alive) setError(String(err && err.message || err)); });
        load();
        const timer = setInterval(load, 15000);
        return () => { alive = false; clearInterval(timer); };
      }, [tick]);

      const summary = data && data.summary || {};
      const attempts = data && data.attempts || {};
      const changes = (data && data.changes || [])
        .slice()
        .sort((a, b) => String(b.created_at || '').localeCompare(String(a.created_at || '')))
        .slice(0, 4);
      const activeStates = new Set(['RUNNING', 'VERIFYING', 'VERIFY_BLOCKED', 'REWORK_READY']);
      const tasks = (data && data.tasks || [])
        .filter((t) => activeStates.has(String(t.state || '')))
        .slice(0, 3);
      const box = {
        border: '1px solid var(--dsh-color-border, #d9dee5)',
        borderRadius: 8, padding: '6px 10px', margin: '4px 0',
        fontSize: 12, color: 'var(--dsh-color-text, #1f2328)',
        background: 'var(--dsh-color-surface, transparent)',
      };
      return h('div', { style: box, 'data-harness-panel': '1' },
        h('div', { style: { display: 'flex', gap: 8, alignItems: 'center' } },
          h('strong', null, 'harness'),
          error
            ? h('span', { style: badge('#b42318') }, 'доска offline')
            : h('span', { style: badge('#067647') }, 'live'),
          h('span', { style: { opacity: 0.7 } },
            data
              ? 'changes ' + (summary.changes || 0) + ' · tasks ' + (summary.tasks || 0)
                + ' · rework ' + (attempts.rework_rate || 0)
              : 'нет данных'),
          error && data ? h('span', { style: { opacity: 0.7 } }, 'stale') : null,
          h('button', {
            type: 'button',
            onClick: () => setTick((n) => n + 1),
            style: { marginLeft: 'auto', cursor: 'pointer', fontSize: 11 },
          }, 'обновить'),
        ),
        error
          ? h('div', { style: { opacity: 0.75, marginTop: 4 } },
              'Запусти: python -m harness board --serve --port 8765')
          : null,
        changes.length
          ? h('div', { style: { marginTop: 4 } },
              changes.map((c) => h('div', { key: c.change_id },
                h('code', null, c.change_id), ' — ', c.state || c.status || '')))
          : null,
        tasks.length
          ? h('div', { style: { marginTop: 4, opacity: 0.85 } },
              tasks.map((t) => h('div', { key: t.task_id },
                h('code', null, t.task_id), ' — ', t.state || '')))
          : null,
      );
    }

    return {
      inject: ['slots'],
      apply(ctx) {
        ctx.slots.inject('conversation.composer.dock', () => ctx.slots.register({
          name: 'conversation.composer.dock', id: 'harness-panel', order: 20,
        }, HarnessPanel));
      },
    };
  },
});
