# Tasks

- [x] 1.1 Модель: `harness/cli.py` (`--service-write`, `write` в services), `harness/brief.py` (валидация `service:` allowed_files), `schemas/brief.schema.json`; проверка `python -m unittest tests.test_specbrief -v`
- [x] 1.2 Policy/guard: `harness/policy.py` (`resolve_target`, `can_write_services`), `harness/hooks.py`, `plugins/harness-guard/guard-core.mjs`, `plugins/harness-guard/index.js`, `policies/policy.json`; проверка `python -m unittest tests.test_parity -v`
- [x] 1.3 Gateway/scope: `harness/gateway.py` (резолв сервисного корня и запись), `harness/scope.py` (services в scope), `harness/fsm.py` (публикация), `tests/test_harness.py`; проверка `python -m unittest tests.test_harness.GatewayTests -v`
- [x] 1.4 Документация: `README.md`, `docs/ARCHITECTURE.md`, `docs/ROADMAP.md`, `docs/PROCESS.md`, `docs/FACTS.md`; проверка `python -m unittest tests.test_agents_contract -v`
