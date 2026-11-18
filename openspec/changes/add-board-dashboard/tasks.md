# Tasks: доска как продукт

- [x] 1.1 Плагин: `plugins/harness-panel/client.js` — main `harness-board`, фильтры, кнопка; проверка `node tests/panel_client_apply.test.mjs`
- [x] 1.2 HTML: `harness/metrics.py` — тёмная тема, KPI, очередь, фильтры, поиск; проверка `python -m unittest tests.test_metrics -v`
- [x] 1.3 Документация: `plugins/harness-panel/docs/DETAIL.md`, спека; проверка `python -m harness change trace --change add-board-dashboard`
- [x] 1.4 Проверка: `chrome_open` + клик «Все», `node tests/panel_client_apply.test.mjs`, `python -m unittest discover -s tests -q`
