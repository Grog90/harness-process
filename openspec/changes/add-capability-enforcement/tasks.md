# Tasks: исполняемые can_read и may_*

- [x] 1.1 Policy: `check_capability`, `check_read` в `harness/policy.py`; проверка `python -m unittest tests.test_harness.PolicyTests -v`
- [x] 1.2 CLI: capability в `cmd_dispatch/cmd_commit/cmd_verdict/cmd_finalize/cmd_run`; проверка `python -m unittest tests.test_harness.CapabilityCLITests -v`
- [x] 1.3 Хук/guard: `policy.read_scope` (+`list_dir`), `checkRead` в `plugins/harness-guard/guard-core.mjs`; проверка `python -m unittest tests.test_harness.GatewayTests tests.test_parity -v`
- [x] 1.4 Тесты/доки: worker→dispatch/commit/verdict/finalize deny, can_read deny/allow, parity restricted; R-CAP-1 и R-READ-1 в rules.json, матрица перегенерирована; проверка полного discover
