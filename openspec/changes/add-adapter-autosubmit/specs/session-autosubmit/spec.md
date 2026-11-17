# Spec Delta

## Purpose

Дать оператору opt-in автоматический submit после turn, не автоматизируя
verdict/commit/accept.

## ADDED Requirements

### Requirement: [REQ-AUTO-1] Opt-in маркер авто-submit

`harness session attach --task <id> --auto-submit` ДОЛЖЕН (SHALL) записать
`.harness/session-auto-submit.json` с `task_id`; без флага маркер удаляется.

#### Scenario: [CASE-AUTO-1-1] Маркер создаётся

- **WHEN** attach выполнен с `--auto-submit`
- **THEN** маркер содержит task_id; при detach маркер удалён

### Requirement: [REQ-AUTO-2] Один submit в конце turn

При `agent/turn-stopping` плагин ДОЛЖЕН (SHALL), если маркер и scope указывают на
одну задачу и агент корневой, вызвать `harness submit --task --attempt` один раз;
маркер снимается после попытки; ошибка пишется в `.harness/session-attach.error`.

#### Scenario: [CASE-AUTO-2-1] Успешный submit

- **WHEN** задача RUNNING, маркер есть, turn завершается
- **THEN** вызывается submit с attempt из scope, маркер удаляется

#### Scenario: [CASE-AUTO-2-2] Ошибка submit

- **WHEN** submit завершился ошибкой
- **THEN** причина в error-файле, маркер удалён, сессия не падает

#### Scenario: [CASE-AUTO-2-3] Не корневой агент

- **WHEN** turn-stopping приходит для субагента
- **THEN** submit не вызывается

#### Scenario: [CASE-AUTO-2-4] Маркер и scope расходятся

- **WHEN** маркер указывает на одну задачу, а scope — на другую
- **THEN** submit не вызывается; маркер остаётся оператору для разбора

### Requirement: [REQ-AUTO-3] Гейты не расширяются

Авто-submit НЕ ДОЛЖЕН (MUST NOT) выносить verdict, commit или accept; он только
переводит задачу в проверку.

#### Scenario: [CASE-AUTO-3-1] Только submit

- **WHEN** проверяется listener
- **THEN** вызывается только `harness submit`, без verdict/commit/accept
