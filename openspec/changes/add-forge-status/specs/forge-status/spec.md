# Spec Delta

## Purpose

Связать PR и checks с решением harness о приёмке, не угадывая статус.

## ADDED Requirements

### Requirement: [REQ-FORGE-1] Статус PR читается и сводится

`harness forge status --task T` (или `--branch B`) ДОЛЖЕН (SHALL) найти PR ветки
`harness/T` через `gh` и вернуть `ok/failing/pending/mergeable/reviewDecision`;
без `gh` или PR — отказ `R-FORGE-1`. Понимаются обе формы checks: CheckRun
(`status`/`conclusion`) и StatusContext (`state`).

#### Scenario: [CASE-FORGE-1-1] Успешные checks

- **WHEN** все checks завершены с SUCCESS
- **THEN** `ok=true`, `failing=[]`, `pending=[]`

#### Scenario: [CASE-FORGE-1-2] Упавший check

- **WHEN** один check FAILURE
- **THEN** `ok=false`, имя в `failing`

#### Scenario: [CASE-FORGE-1-3] Нет gh

- **WHEN** `gh` отсутствует
- **THEN** отказ `R-FORGE-1`, пустой «ок» не возвращается

### Requirement: [REQ-FORGE-2] Ожидание checks

`--wait` ДОЛЖЕН (SHALL) ждать до `--timeout`; таймаут — отказ с pending/url.

#### Scenario: [CASE-FORGE-2-1] Проверки завершились

- **WHEN** pending сменяется на успех
- **THEN** команда возвращает ok

#### Scenario: [CASE-FORGE-2-2] Таймаут

- **WHEN** checks не завершились за timeout
- **THEN** отказ `R-FORGE-1`, в деталях pending
