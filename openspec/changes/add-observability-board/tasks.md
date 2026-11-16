# Tasks

- [x] 1.1 Метрики: `harness/metrics.py` (collect, markdown, HTML, board_data), `tests/test_metrics.py`; проверка `python -m unittest tests.test_metrics -v`
- [x] 1.2 CLI: `harness/cli.py` (metrics, board, ci), `harness/ci.py`; проверка `python -m harness ci --report .harness/tmp/ci.json`
- [x] 1.3 Документация: `docs/CI.md`, `README.md`, `docs/ARCHITECTURE.md`, `docs/ROADMAP.md`, `docs/PROCESS.md`, `docs/FACTS.md`; проверка `python -m unittest tests.test_agents_contract -v`
- [x] 1.4 Живая доска: `harness/metrics.py` (`make_server`), `harness/cli.py` (`board --serve`), `tests/test_metrics.py`; проверка `python -m unittest tests.test_metrics -v`
- [x] 1.5 OTLP: `harness/otel.py`, `harness/cli.py` (`otel`), `tests/test_otel.py`; проверка `python -m unittest tests.test_otel -v`
- [x] 1.6 Headless smoke: `tools/headless_smoke.py`, `docs/CI.md`; проверка `python tools/headless_smoke.py --out ...`
