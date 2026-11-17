# Предложение: OTel spans задачи

## Problem

`harness otel` отдаёт только OTLP-метрики. Не видно, сколько занял каждый переход
задачи и где она стояла; `trace_id` в журнале нет.

## What Changes

- `harness/otel.py`: `trace_id`, `build_traces` — OTLP spans из событий задачи;
- один trace на задачу, span на событие; время — `created_at`, конец — следующий event;
- статус span: OK/ERROR по `result.ok`;
- CLI `harness otel --traces-task T` добавляет `resourceSpans` к метрикам.

## Impact

- Класс риска: **L1** (read-only экспорт; новых мутаций нет).
- Детерминированные trace/span id: можно сопоставить прогоны.
- Событий без результата получают статус UNSET, а не ложный OK.

## Out of Scope

- OTLP/gRPC и protobuf.
- Автоматическая отправка spans в collector из каждого перехода (экспорт по команде).
- Span links между разными задачами.
