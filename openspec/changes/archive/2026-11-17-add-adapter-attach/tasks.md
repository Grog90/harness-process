# Tasks: автопривязка сессии

- [x] 1.1 Модуль: `harness/session.py` — attach/read/detach; проверка `python -m unittest tests.test_session_attach -v`
- [x] 1.2 CLI: `harness session attach|detach|status` в `harness/cli.py`; проверка `python -m harness session status`
- [x] 1.3 Плагин: `plugins/harness-session/session-core.mjs` readAttach/dispatchArgs, `plugins/harness-session/index.js` agent/created; проверка `node --test plugins/harness-session/session-core.test.mjs`
- [x] 1.4 Тесты и документация: attach существующей/неизвестной задачи, dispatchDecision, consumeAttach, writeAttachError, detach; `plugins/harness-session/README.md` и `docs/PLUGIN.md`; проверка `python -m unittest tests.test_session_attach -v`
