# Spec Delta

## Purpose

Дать исполняемую интеграционную проверку нескольких сервисов до приёмки.

## ADDED Requirements

### Requirement: [REQ-E2E-1] Прогон на копиях сервисов

`harness e2e run` ДОЛЖЕН (SHALL) скопировать каждый `--service` во временный
каталог, выставить `HARNESS_E2E_<NAME>_DIR/_SHA`, выполнить `--command` и записать
отчёт с exit/duration/services/stdout_tail.

#### Scenario: [CASE-E2E-1-1] Успешный прогон

- **WHEN** два сервиса и команда проверяет env
- **THEN** `ok=true`, отчёт содержит оба SHA

#### Scenario: [CASE-E2E-1-2] Сервис не найден

- **WHEN** путь сервиса не существует
- **THEN** отказ `R-E2E-1`, команда не запускается

#### Scenario: [CASE-E2E-1-3] SHA — это коммит

- **WHEN** задан `name=PATH@SHA`
- **THEN** копия делается `git archive SHA`, а не из грязного worktree

#### Scenario: [CASE-E2E-1-4] Worktree без SHA

- **WHEN** SHA не задан
- **THEN** копируется worktree, в отчёте `dirty=true`, если есть незакоммиченные правки

### Requirement: [REQ-E2E-2] Ошибка и таймаут

Ненулевой exit или таймаут ДОЛЖНЫ (MUST) давать `ok=false` с кодом/флагом в
отчёте, а не молчаливый успех.

#### Scenario: [CASE-E2E-2-1] Ненулевой exit

- **WHEN** команда завершилась с кодом 3
- **THEN** `ok=false`, `exit_code=3`, отчёт записан

#### Scenario: [CASE-E2E-2-2] Таймаут

- **WHEN** команда не уложилась в `--timeout`
- **THEN** `ok=false`, `timed_out=true`

### Requirement: [REQ-E2E-3] Только операторские роли

e2e ДОЛЖЕН (MUST) требовать `may_commit`; worker не запускает интеграцию.

#### Scenario: [CASE-E2E-3-1] Worker не запускает

- **WHEN** роль без `may_commit` вызывает e2e
- **THEN** отказ `R-CAP-1`
