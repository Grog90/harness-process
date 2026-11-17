# Tasks: forge status

- [x] 1.1 Модуль: `harness/forge.py` — pr_status/wait_status/summarize_checks; проверка `python -m unittest tests.test_forge -v`
- [x] 1.2 CLI: `harness forge status --task|--branch [--wait --timeout]` в `harness/cli.py`; проверка `python -m harness forge --help`
- [x] 1.3 Тесты: успех/падение/pending, нет gh, нет PR, wait/timeout в `tests/test_forge.py`; проверка `python -m unittest tests.test_forge -v`
- [x] 1.4 Доки: `docs/CI.md`, `docs/DEPLOY.md`; проверка полного discover
