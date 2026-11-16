# Tasks

- [x] 1.1 Прогоны: `harness/verification.py` (baseline/candidate, разбор, diff, отчёт), `tests/test_verification.py`; проверка `python -m unittest tests.test_verification -v`
- [x] 1.2 Гейты и записи: `harness/changefsm.py` (verify/release/accept/archive, release.json/acceptance.json/closing.json, evidence-манифест), `harness/cli.py` (verification run|show, change release|accept|close|evidence), `tests/test_changefsm.py`; проверка `python -m unittest tests.test_changefsm -v`
- [x] 1.3 Правила: `harness/selftest.py`, `policies/rules.json`, `docs/RULES_MATRIX.md`; проверка `python -m harness selftest && python -m harness coverage`
- [x] 1.4 Документация: `README.md`, `docs/ARCHITECTURE.md`, `docs/ROADMAP.md`, `docs/PROCESS.md`, `docs/FACTS.md`; проверка `python -m unittest tests.test_agents_contract -v`
