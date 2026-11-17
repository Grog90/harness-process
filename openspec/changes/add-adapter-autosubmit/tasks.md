# Tasks: opt-in авто-submit

- [x] 1.1 Маркер: `harness/session.py` — auto_submit/read/consume в attach/detach; проверка `python -m unittest tests.test_session_attach -v`
- [x] 1.2 CLI: `harness session attach --auto-submit` в `harness/cli.py`; проверка `python -m harness session attach --help`
- [x] 1.3 Плагин: `plugins/harness-session/session-core.mjs` autoSubmitArgs/autoSubmitDecision/read/consume, `plugins/harness-session/index.js` agent/turn-stopping; проверка `node --test plugins/harness-session/session-core.test.mjs`
- [x] 1.4 Тесты/доки: маркер, submit args, решение autoSubmitDecision; `docs/PLUGIN.md` и `plugins/harness-session/README.md`; проверка `python -m unittest tests.test_session_attach -v`
