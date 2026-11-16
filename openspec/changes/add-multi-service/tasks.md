# Tasks

- [x] 1.1 Сервисы change: `harness/cli.py` (`--service`, `change services`), `harness/specbrief.py` (services в брифе), `harness/brief.py` (валидация), `schemas/brief.schema.json`; проверка `python -m unittest tests.test_changefsm tests.test_specbrief -v`
- [x] 1.2 Мультисервисный verification: `harness/verification.py` (`service:mode`, группы, `--cwd`), `tests/test_verification.py`; проверка `python -m unittest tests.test_verification -v`
- [x] 1.3 Merge order и записи: `harness/changefsm.py` (`_order_problem`, merge_order в release/acceptance), `tests/test_changefsm.py`; проверка `python -m unittest tests.test_changefsm -v`
- [x] 1.3 Документация: `README.md`, `docs/ARCHITECTURE.md`, `docs/ROADMAP.md`, `docs/PROCESS.md`, `docs/FACTS.md`; проверка `python -m unittest tests.test_agents_contract -v`
