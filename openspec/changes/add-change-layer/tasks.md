# Tasks

- [x] 1.1 Состояние и автомат изменения: таблицы `changes`/`change_events`/`change_reviews`, `tasks.change_id`, модуль `harness/changefsm.py`, тесты `tests/test_changefsm.py`; проверка `python -m unittest tests.test_changefsm -v`
- [x] 1.2 CLI и схема брифа: `harness/cli.py` (change new/list/show/review/trace/ready/advance, task new --change), `schemas/brief.schema.json` (change_id, spec_ref); проверка `python -m harness change --help`
- [x] 1.3 Мост спека ↔ бриф и гейт коммита: `harness/specbrief.py` (REQ/CASE ID, change_id, spec_ref, полный SHA), `harness/brief.py` (валидация change_id/spec_ref), `harness/specgate.py` (только живой change, сверка digest), `harness/fsm.py` (гейт диспетча); проверка `python -m unittest tests.test_specbrief tests.test_specgate -v`
- [x] 1.4 Правила и проверки: `harness/selftest.py` (R-CHG-1, R-REF-1, R-REV-1, R-REV-2, R-TRACE-2), `policies/rules.json`, `docs/RULES_MATRIX.md`; проверка `python -m harness selftest && python -m harness coverage`
- [x] 1.5 Документация: `README.md`, `docs/ARCHITECTURE.md`, `docs/ROADMAP.md`, `docs/PROCESS.md`; проверка `python -m unittest tests.test_agents_contract -v`
