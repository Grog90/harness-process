# Spec Delta

## Purpose

Показать переходы задачи как OTLP spans, а не только счётчики.

## ADDED Requirements

### Requirement: [REQ-SPAN-1] Spans из событий задачи

`build_traces(events, task_id)` ДОЛЖЕН (SHALL) строить один trace на задачу:
`traceId` детерминирован, `spanId` — на событие, время — `created_at`, конец —
следующее событие (или +1 мс).

#### Scenario: [CASE-SPAN-1-1] Три события

- **WHEN** есть dispatch/submit/commit с временами 100/101/102
- **THEN** три span с одним traceId, длительности 1с, status OK/ERROR/OK

#### Scenario: [CASE-SPAN-1-2] Пустая задача

- **WHEN** событий нет
- **THEN** `spans=[]`, форма OTLP сохраняется

### Requirement: [REQ-SPAN-2] CLI добавляет spans

`harness otel --traces-task T` ДОЛЖЕН (SHALL) добавить `resourceSpans` к
метрическому payload; без флага поведение прежнее.

#### Scenario: [CASE-SPAN-2-1] Экспорт с задачей

- **WHEN** указан `--traces-task T`
- **THEN** payload содержит `resourceSpans` со spans T
