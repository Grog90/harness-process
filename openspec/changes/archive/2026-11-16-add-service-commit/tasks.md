# Tasks: коммит и tree-snapshot сервисов

- [x] 1.1 Snapshot: `compute_tree`, `snapshot_for_task` в `harness/snapshot.py`; подключить в `fsm.py`, `cli.py`, `run.py`, `finalize.py`; проверка `python -m unittest tests.test_harness.FinalizeTests -v`
- [x] 1.2 Finalize: `commit_services` в `harness/finalize.py` (write-set по сервису, commit, SHA; in-root → главный коммит); `harness/state.py` — `commits_for`; проверка `python -m unittest tests.test_harness.FinalizeTests -v`
- [x] 1.3 Evidence: `task_commits` в `harness/changefsm.py`; проверка `python -m unittest tests.test_harness.FinalizeTests -v`
- [x] 1.4 Тесты: снимок сервиса, read-only исключён, коммит сервиса, посторонний файл R-WRITESET-1, end-to-end finalize, task_commits в `tests/test_harness.py`; проверка `python -m unittest discover -s tests -q`
- [x] 1.5 Документация: `README.md`, `docs/ARCHITECTURE.md`, `docs/BACKLOG.md`, `docs/STATUS.md`, `docs/PLUGIN.md`; проверка `python -m unittest tests.test_agents_contract -v`
