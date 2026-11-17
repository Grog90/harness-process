# Tasks: evidence export

- [x] 1.1 Модуль: `harness/evidence_export.py` export/verify/sha256; проверка `python -m unittest tests.test_evidence_export -v`
- [x] 1.2 CLI: `harness evidence export|verify --dest` в `harness/cli.py`; проверка `python -m harness evidence --help`
- [x] 1.3 Тесты: export/verify, порча, нет источника/манифеста в `tests/test_evidence_export.py`; проверка `python -m unittest tests.test_evidence_export -v`
- [x] 1.4 Доки: `docs/CI.md`, `docs/DEPLOY.md`; проверка полного discover
