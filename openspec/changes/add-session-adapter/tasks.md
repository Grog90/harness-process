# Tasks: адаптер DSH ↔ harness

- [x] 1.1 Ядро: `plugins/harness-session/session-core.mjs` — resolveHarnessRoot, readScope, taskContext, buildStatusArgs; проверка `node --test plugins/harness-session/session-core.test.mjs`
- [x] 1.2 Плагин: `plugins/harness-session/index.js`, `plugins/harness-session/cordis.patch.yml`, `plugins/harness-session/package.json`; контекст + инструмент `harness_task`; проверка `node --check plugins/harness-session/index.js`
- [x] 1.3 Тесты: Python-обёртка `tests/test_session_adapter.py` над node-тестами; проверка `python -m unittest tests.test_session_adapter -v`
- [x] 1.4 Документация: `plugins/harness-session/README.md`, `README.md`, `docs/PLUGIN.md`, `docs/BACKLOG.md`; проверка `python -m unittest tests.test_agents_contract -v`
