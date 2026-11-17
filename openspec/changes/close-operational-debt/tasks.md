# Tasks: операционные долги

- [x] 1.1 Панель: host-route в `plugins/harness-panel/index.js`, same-origin в `client.js`; проверка `python -m unittest tests.test_panel_plugin -v`
- [x] 1.2 Public: `SECURITY.md`, `LICENSE`, `docs/FINALIZER.md`, `README.md`; проверка `gh api repos/Grog90/harness-process --jq .security_and_analysis`
- [x] 1.3 Attest: `harness/attest.py`, `harness attest`; проверка `python -m unittest tests.test_attest -v`
- [x] 1.4 Проверка: `node tests/panel_host_route.test.mjs`, полный discover, CI; live-скриншот в `.desktop/` (не коммитится)
