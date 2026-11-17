# Spec Delta

## Purpose

Сделать ролевые ограничения исполняемыми: `can_read` и `may_*` перестают быть
описанием и начинают блокировать чтение и переходы.

## ADDED Requirements

### Requirement: [REQ-CAP-1] may_* исполняются в CLI

`harness dispatch|commit|verdict|finalize` ДОЛЖНЫ (MUST) проверять
`may_dispatch/may_commit/may_verify` роли из `guard-scope`/env до выполнения и
отказывать с `R-ROLE-1`.

#### Scenario: [CASE-CAP-1-1] Worker не диспетчит

- **WHEN** `guard-scope.role=worker` и вызван `harness dispatch`
- **THEN** отказ `R-ROLE-1`, переход не выполняется

#### Scenario: [CASE-CAP-1-2] Verifier проверяет

- **WHEN** `guard-scope.role=verifier` и вызван `harness verdict`
- **THEN** проверка `may_verify` проходит

#### Scenario: [CASE-CAP-1-3] Orchestrator коммитит

- **WHEN** `HARNESS_ROLE=orchestrator` (или scope primary) и вызван `harness commit`
- **THEN** проверка `may_commit` проходит

#### Scenario: [CASE-CAP-1-4] Finalize требует may_commit

- **WHEN** роль без `may_commit` вызывает `harness finalize`
- **THEN** отказ `R-ROLE-1`

### Requirement: [REQ-CAP-2] can_read исполняется в хуке и guard

Чтение `read_file/glob/grep` ДОЛЖНО (MUST) проверяться против `can_read` роли в
Python-хуке и JS-guard; путь вне `can_read` — `R-READ-1`.

#### Scenario: [CASE-CAP-2-1] Чтение вне can_read

- **WHEN** у роли `can_read=["docs/**"]`, а путь `src/a.py`
- **THEN** отказ `R-READ-1`

#### Scenario: [CASE-CAP-2-2] Чтение внутри can_read

- **WHEN** путь `docs/plan.md`
- **THEN** чтение разрешено

### Requirement: [REQ-CAP-3] Поведение ролей не расширяется

Проверки НЕ ДОЛЖНЫ (MUST NOT) давать рабочей роли новые права. `human` сохраняет
dispatch/commit; `verdict` остаётся у verifier; `system` не получает may_*.

#### Scenario: [CASE-CAP-3-1] Human сохраняет dispatch/commit

- **WHEN** роль `human` вызывает dispatch/commit
- **THEN** capability проходит; verdict у human отклоняется (may_verify=false)
