# Design: OTel spans

## Decisions

**1. Один trace на задачу.**
`trace_id = sha256(task_id)[:32]`; `span_id = sha256(event_id)[:16]`. Это
детерминированно: один и тот же прогон можно сопоставить без хранения ID.

**2. Время из журнала.**
Начало span — `events.created_at`; конец — следующий event или +1 мс. Это не
измерение длительности операции, а интервал между переходами; так честнее, чем
выдумывать end time.

**3. Метрики и spans вместе.**
`--traces-task` добавляет `resourceSpans` к существующему `resourceMetrics` в
одном OTLP payload. Без флага — прежний формат.

## Risks

- Длительность span — интервал до следующего события, а не CPU-время.
- События без `result.ok` имеют UNSET, а не OK.
- OTLP/HTTP JSON без protobuf/gRPC.
