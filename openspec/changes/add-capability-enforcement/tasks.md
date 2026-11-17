# Tasks: исполняемые can_read и may_*

- [x] 1.1 Policy: `check_capability`, `check_read` в `harness/policy.py`; проверка `python -m unittest tests.test_harness.PolicyTests -v`
- [x] 1.2 CLI: capability в `cmd_dispatch/cmd_commit/cmd_verdict/cmd_finalize`; проверка `python -m unittest tests.test_harness.CapabilityCLITests -v`
- [x] 1.3 Хук/guard: `policy.read_scope`, `checkRead` в `plugins/harness-guard/guard-core.mjs`; проверка `python -m unittest tests.test_harness.GatewayTests -v`
- [x] 1.4 Тесты/доки: worker→dispatch/commit/verdict deny, can_read deny, human allow; `docs/PROCESS.md`; проверка полного discover
