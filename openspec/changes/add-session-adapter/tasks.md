# Tasks: адаптер DSH ↔ harness

- [x] 1.1 Ядро: `session-core.mjs` — resolveRoot, readScope, taskContext, buildStatusArgs; проверка `node --test plugins/harness-session/session-core.test.mjs`
- [x] 1.2 Плагин: `index.js`, `cordis.patch.yml`, `package.json`; контекст + инструмент `harness_task`; проверка `node --check plugins/harness-session/index.js`
- [x] 1.3 Тесты: Python-обёртка над node-тестами; проверка `python -m unittest tests.test_session_adapter -v`
- [x] 1.4 Документация: README/PLUGIN/BACKLOG и README плагина; проверка `python -m unittest tests.test_agents_contract -v`
