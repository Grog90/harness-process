window.__ModuleLoader__.load({
  id: '@local/harness-panel',
  factory(require) {
    const React = require('react');
    const h = React.createElement;
    const DEFAULT_URL = '/harness-panel/metrics.json';

    function badge(color) {
      return {
        display: 'inline-block', padding: '0 7px', borderRadius: 999,
        fontSize: 11, lineHeight: '18px', background: color, color: '#fff',
        whiteSpace: 'nowrap',
      };
    }

    function statusColor(status) {
      const s = String(status || '');
      if (s === 'ARCHIVED' || s === 'ACCEPTED') return '#067647';
      if (s === 'CANCELLED') return '#667085';
      if (s.indexOf('BLOCK') >= 0 || s === 'ESCALATED') return '#b42318';
      if (s === 'READY') return '#175cd3';
      return '#b54708';
    }

    function useBoard() {
      const [data, setData] = React.useState(null);
      const [error, setError] = React.useState('');
      const [tick, setTick] = React.useState(0);
      React.useEffect(() => {
        let alive = true;
        const load = () => fetch(DEFAULT_URL, { cache: 'no-store' })
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
      return { data, error, reload: () => setTick((n) => n + 1) };
    }

    let openBoard = () => {};

    function filterChanges(changes, filter, query) {
      const needle = String(query || '').toLowerCase();
      return (changes || []).filter((c) => {
        const s = String(c.status || '');
        if (filter === 'active' && (s === 'ACCEPTED' || s === 'ARCHIVED' || s === 'CANCELLED')) return false;
        if (filter === 'ready' && s !== 'READY') return false;
        if (filter === 'blocked' && s.indexOf('BLOCK') < 0 && s !== 'ESCALATED') return false;
        if (filter === 'archive' && s !== 'ACCEPTED' && s !== 'ARCHIVED') return false;
        if (needle && String(c.change_id || '').toLowerCase().indexOf(needle) < 0) return false;
        return true;
      });
    }

    function Stat(props) {
      return h('div', {
        style: {
          flex: '1 1 150px', minWidth: 140, padding: '12px 14px',
          border: '1px solid var(--dsh-color-border, #d9dee5)', borderRadius: 10,
          background: 'var(--dsh-color-surface, transparent)',
        },
      },
        h('div', { style: { fontSize: 24, fontWeight: 650, lineHeight: 1.2 } }, props.value),
        h('div', { style: { fontSize: 12, opacity: 0.65, marginTop: 2 } }, props.label),
      );
    }

    function HarnessPanel() {
      const { data, error, reload } = useBoard();
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
            type: 'button', onClick: openBoard,
            style: { marginLeft: 'auto', cursor: 'pointer', fontSize: 11 },
          }, 'открыть доску'),
          h('button', {
            type: 'button', onClick: reload,
            style: { cursor: 'pointer', fontSize: 11 },
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

    function HarnessBoard() {
      const { data, error, reload } = useBoard();
      const [filter, setFilter] = React.useState('active');
      const [query, setQuery] = React.useState('');
      const summary = data && data.summary || {};
      const attempts = data && data.attempts || {};
      const all = data && data.changes || [];
      const visible = filterChanges(all, filter, query);
      const tabs = [
        ['active', 'Активные'], ['ready', 'READY'], ['blocked', 'Блок'],
        ['archive', 'Архив'], ['all', 'Все'],
      ];
      const tile = (value, label) => h(Stat, { value, label });
      const btn = (key, label) => h('button', {
        key, type: 'button', onClick: () => setFilter(key),
        style: {
          padding: '5px 11px', borderRadius: 999, cursor: 'pointer', fontSize: 12,
          border: '1px solid var(--dsh-color-border, #d9dee5)',
          background: filter === key ? 'var(--dsh-color-accent, #2f5fd0)' : 'transparent',
          color: filter === key ? '#fff' : 'inherit',
        },
      }, label);
      return h('div', {
        style: {
          maxWidth: 1120, margin: '0 auto', padding: '24px 28px 48px',
          color: 'var(--dsh-color-text, #1f2328)',
        },
      },
        h('div', { style: { display: 'flex', alignItems: 'baseline', gap: 12, flexWrap: 'wrap' } },
          h('h1', { style: { fontSize: 24, margin: 0 } }, 'Доска процесса'),
          error ? h('span', { style: badge('#b42318') }, 'offline') : h('span', { style: badge('#067647') }, 'live'),
          h('span', { style: { opacity: 0.6, fontSize: 12 } }, data ? data.generated_at : ''),
          h('button', {
            type: 'button', onClick: reload,
            style: { marginLeft: 'auto', cursor: 'pointer', fontSize: 12 },
          }, 'обновить'),
        ),
        h('div', { style: { display: 'flex', gap: 10, flexWrap: 'wrap', margin: '18px 0' } },
          tile(summary.tasks_active || 0, 'активных задач'),
          tile(summary.changes || 0, 'change всего'),
          tile(attempts.rework_rate || 0, 'rework rate'),
          tile(summary.open_findings || 0, 'открытых находок'),
        ),
        h('div', { style: { display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center', marginBottom: 12 } },
          tabs.map(([k, label]) => btn(k, label)),
          h('input', {
            value: query, onChange: (e) => setQuery(e.target.value),
            placeholder: 'поиск по change…',
            style: {
              marginLeft: 'auto', minWidth: 220, padding: '6px 10px', fontSize: 12,
              border: '1px solid var(--dsh-color-border, #d9dee5)', borderRadius: 8,
              background: 'transparent', color: 'inherit',
            },
          }),
        ),
        error
          ? h('div', { style: { padding: 16, border: '1px solid #b42318', borderRadius: 10 } },
              'Доска недоступна: ' + error + '. Запусти python -m harness board --serve --port 8765')
          : null,
        h('div', { style: { display: 'flex', flexDirection: 'column', gap: 6 } },
          visible.map((c) => h('div', {
            key: c.change_id,
            style: {
              display: 'grid', gridTemplateColumns: '1.5fr 130px 60px 60px 70px',
              gap: 10, alignItems: 'center', padding: '9px 12px', fontSize: 13,
              border: '1px solid var(--dsh-color-border, #d9dee5)', borderRadius: 9,
              background: 'var(--dsh-color-surface, transparent)',
            },
          },
            h('code', { style: { fontSize: 12 } }, c.change_id),
            h('span', null, h('span', { style: badge(statusColor(c.status)) }, c.status)),
            h('span', { style: { opacity: 0.7 } }, c.risk_class || '—'),
            h('span', { style: { opacity: 0.7 } }, (c.reviews || 0) + ' rev'),
            h('span', { style: { opacity: 0.7 } },
              c.verification && c.verification.ok ? 'verify ✓' : 'verify —'),
          )),
          !error && !visible.length
            ? h('div', { style: { padding: 24, opacity: 0.6, textAlign: 'center' } }, 'Ничего не найдено')
            : null,
        ),
        h('div', { style: { marginTop: 18, fontSize: 12, opacity: 0.6 } },
          'Данные: /harness-panel/metrics.json · обновление каждые 15 с · только чтение'),
      );
    }

    return {
      inject: ['slots'],
      filterChanges,
      openBoard: () => openBoard(),
      apply(ctx) {
        openBoard = () => {
          try {
            const layout = ctx.get ? ctx.get('layout') : ctx.layout;
            if (layout && typeof layout.selectPanel === 'function') {
              layout.selectPanel('harness-board');
            }
          } catch (err) {
            /* layout недоступен — кнопка просто не переключает панель */
          }
        };
        try {
          ctx.slots.inject('conversation.composer.dock', () => ctx.slots.register({
            name: 'conversation.composer.dock', id: 'harness-panel', order: 20,
          }, HarnessPanel));
        } catch (err) { /* панель не критична для чата */ }
        try {
          ctx.slots.inject('main', () => ctx.slots.register({
            name: 'main', key: 'harness-board',
          }, HarnessBoard));
        } catch (err) { /* main slot может быть недоступен в этой сборке */ }
      },
    };
  },
});
