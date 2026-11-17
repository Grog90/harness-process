# Tasks: multi-service e2e

- [x] 1.1 Модуль: `harness/e2e.py` parse/run/отчёт; проверка `python -m unittest tests.test_e2e -v`
- [x] 1.2 CLI: `harness e2e run --change --command --service` в `harness/cli.py`; проверка `python -m harness e2e --help`
- [x] 1.3 Тесты: успех/failure/timeout/worker/archive SHA/missing service/bad change в `tests/test_e2e.py`; проверка `python -m unittest tests.test_e2e -v`
- [x] 1.4 Доки/правило: `docs/DEPLOY.md`, `R-E2E-1`, матрица; проверка полного discover
