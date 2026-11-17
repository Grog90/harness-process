# Tasks: автопривязка сессии

- [ ] 1.1 Модуль: `harness/session.py` — attach/read/detach; проверка `python -m unittest tests.test_session_attach -v`
- [ ] 1.2 CLI: `harness session attach|detach|status` в `harness/cli.py`; проверка `python -m harness session status`
- [ ] 1.3 Плагин: `plugins/harness-session/session-core.mjs` readAttach/dispatchArgs, `plugins/harness-session/index.js` agent/created; проверка `node --test plugins/harness-session/session-core.test.mjs`
- [ ] 1.4 Тесты и документация: attach существующей/неизвестной задачи, ошибка dispatch, detach; `plugins/harness-session/README.md` и `docs/PLUGIN.md`
