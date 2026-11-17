# Tasks: OTel spans

- [x] 1.1 Модуль: `harness/otel.py` trace_id/build_traces; проверка `python -m unittest tests.test_otel -v`
- [x] 1.2 CLI: `harness otel --traces-task T --traces-out|--traces-endpoint` в `harness/cli.py`; проверка `python -m harness otel --help`
- [x] 1.3 Тесты: spans/trace/status/UNSET/empty и отказ без приёмника в `tests/test_otel.py`; проверка `python -m unittest tests.test_otel -v`
- [x] 1.4 Доки: `docs/CI.md` (OTLP-раздел); проверка полного discover
