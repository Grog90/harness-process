# Tasks

- [x] 1.1 Модель: `harness/cli.py` (`--service-write`, `write` в services), `harness/brief.py` (валидация `service:` allowed_files), `schemas/brief.schema.json` (`write`); проверка `python -m unittest tests.test_specbrief -v`
- [x] 1.2 Policy/guard: `harness/policy.py` (`resolve_target`, `can_write_services`, in-root protected), `harness/hooks.py`, `plugins/harness-guard/guard-core.mjs`, `plugins/harness-guard/index.js`, `policies/policy.json`; проверка `python -m unittest tests.test_parity -v`
- [x] 1.3 Gateway/scope/snapshot: `harness/gateway.py`, `harness/scope.py`, `harness/fsm.py` (сверка services брифа с change), `harness/snapshot.py` (service-ключи), `tests/test_harness.py`, `tests/test_scope.py`, `tests/test_changefsm.py`; проверка `python -m unittest tests.test_harness tests.test_scope tests.test_changefsm -v`
- [x] 1.4 Документация: `README.md`, `docs/ARCHITECTURE.md`, `docs/ROADMAP.md`, `docs/PROCESS.md`, `docs/FACTS.md`, `docs/PLUGIN.md`; проверка `python -m unittest tests.test_agents_contract -v`
