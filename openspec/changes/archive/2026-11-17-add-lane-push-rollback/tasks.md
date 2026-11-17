# Tasks: публикация и откат

- [x] 1.1 Публикация: `harness/lanes.py` push/pr_args/pull_request, CLI `harness lane push|pr`; проверка `python -m unittest tests.test_harness.LaneTests -v`
- [x] 1.2 Откат: `harness/finalize.py` `_rollback_records`, `previous_sha`, обёртка главного коммита; проверка `python -m unittest tests.test_harness.FinalizeTests -v`
- [x] 1.3 БД: `harness/state.py` `delete_commit`; проверка `tests.test_harness.FinalizeTests`
- [x] 1.4 Доки/тесты: `docs/PROCESS.md`, `docs/DEPLOY.md`; проверка `python -m unittest discover -s tests -q`
