# Proposal: доска, метрики и CI-снимок

## Why

Гейты Этапов 1–3 работают, но человек видит процесс только через CLI и БД: очередь
review, застрявшие задачи и состояние verification приходится собирать руками.
CI не имеет одной точки «здоров ли процесс», а метрики (lead time, rework, открытые
находки) не считаются вовсе.

Этап 4 добавляет видимость: `harness metrics` и `harness board` читают те же SQLite
и файлы change, а `harness ci` собирает отчёт для CI/evidence.

## What Changes

- `harness/metrics.py`: метрики изменений, задач, review, verification, evidence,
  lead time и rework; markdown/HTML-рендер.
- `harness/ci.py` и `harness ci`: selftest + покрытие + метрики (+ unittest),
  отчёт `evidence/ci-<ts>/report.json`.
- `harness/metrics.py --serve`: живая локальная доска с `/metrics.json` и `/healthz`.
- `harness/otel.py` и `harness otel`: OTLP/HTTP JSON с метриками процесса.
- `tools/headless_smoke.py`: проверка `dsh headless --json` с evidence.
- CLI `harness metrics`, `harness board --out|--serve`, `harness otel`.
- `docs/CI.md` с примером GitHub Actions и честными границами.

## Impact

- Доска — живой локальный HTTP-сервер или снимок; встраивание в DSH GUI не делается.
- Метрики считаются из состояния, поэтому отдельно «улучшить отчёт» нельзя.
- OTLP — только HTTP JSON, без protobuf/gRPC; агентские трейсы экспортирует DSH.
- Headless проверен до final; в этой установке упирается в `MISSING_CREDENTIAL`.
- CI-отчёт не заменяет change-гейты: release/accept/archive остаются обязательными.

## Out of Scope

- Protobuf/gRPC-экспорт и встраивание доски в DSH GUI.
- Мультирепозиторность (Этап 5).
- Автоматическое открытие PR и комментарии в forge.
