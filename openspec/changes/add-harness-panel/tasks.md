# Tasks: панель harness

- [x] 1.1 Бандл: `plugins/harness-panel/package.json`, `cordis.patch.yml`, `index.js`, `client.js`, locale, icon; проверка `node --check plugins/harness-panel/client.js`
- [x] 1.2 Регистрация: `plugins/harness-panel/client.js` — `conversation.composer.dock` id `harness-panel`; проверка Inspect Slots occupant
- [x] 1.3 Доска/CORS: `Access-Control-Allow-Origin` в `harness/metrics.py`; проверка fetch из GUI
- [x] 1.4 Тесты/доки: `tests/test_panel_plugin.py`, `docs/DEPLOY.md`, `plugins/harness-panel/docs/DETAIL.md`; проверка полного discover
