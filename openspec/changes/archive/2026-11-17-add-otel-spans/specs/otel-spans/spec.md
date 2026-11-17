# Spec Delta

## Purpose

Показать переходы задачи как OTLP spans, а не только счётчики.

## ADDED Requirements

### Requirement: [REQ-SPAN-1] Spans из событий задачи

`build_traces(events, task_id)` ДОЛЖЕН (SHALL) строить один trace на задачу:
`traceId` детерминирован, `spanId` — на событие, время — `created_at`, конец —
следующее событие (или +1 мс). Статус span: OK при `result.ok=true`, ERROR при
`false`, UNSET при отсутствии результата.

#### Scenario: [CASE-SPAN-1-1] Три события

- **WHEN** есть dispatch/submit/commit с временами 100/101/102
- **THEN** три span с одним traceId; первые два интервала 1с, последний +1 мс;
  status OK/ERROR/OK

#### Scenario: [CASE-SPAN-1-2] Пустая задача

- **WHEN** событий нет
- **THEN** `spans=[]`, форма OTLP сохраняется

#### Scenario: [CASE-SPAN-1-3] Нет результата

- **WHEN** у события нет `result.ok`
- **THEN** статус span UNSET (0), а не OK

### Requirement: [REQ-SPAN-2] CLI отдаёт traces отдельным OTLP

`harness otel --traces-task T` ДОЛЖЕН (SHALL) формировать **отдельный** OTLP
traces-payload; запись — `--traces-out`, отправка — `--traces-endpoint`. Смешивать
`resourceMetrics` и `resourceSpans` в одном запросе нельзя: это разные OTLP-сервисы.

#### Scenario: [CASE-SPAN-2-1] Экспорт с задачей

- **WHEN** указаны `--traces-task T` и `--traces-out PATH`
- **THEN** файл содержит только `resourceSpans`, метрики — в `--out`

#### Scenario: [CASE-SPAN-2-2] Spans без приёмника

- **WHEN** `--traces-task T` без `--traces-out`/`--traces-endpoint`
- **THEN** отказ, смешанный payload не создаётся
