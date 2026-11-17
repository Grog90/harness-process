# Spec Delta

## Purpose

Сделать DSH-сессию задачей harness без ручного `harness dispatch`, не отдавая
агенту вердикт, commit и приёмку.

## ADDED Requirements

### Requirement: [REQ-ATTACH-1] Привязка пишется явной командой оператора

`harness session attach --task <id>` ДОЛЖЕН (MUST) проверить существование задачи,
отказать ролям worker/verifier/analyst/qa (разрешены human/system/orchestrator) и
записать `.harness/session-attach.json` с `task_id`, `auto_dispatch=true` и временем;
`session status|detach` показывают/снимают привязку; detach не ограничен ролью —
иначе конфликтную привязку нельзя снять при активном чужом scope.

#### Scenario: [CASE-ATTACH-1-1] Привязка существующей задачи

- **WHEN** задача зарегистрирована и оператор выполнил `session attach --task T`
- **THEN** файл содержит `task_id=T`, `auto_dispatch=true`, `created_at`

#### Scenario: [CASE-ATTACH-1-2] Неизвестная задача

- **WHEN** `session attach --task missing`
- **THEN** команда завершается ошибкой и файл не создаётся

#### Scenario: [CASE-ATTACH-1-3] Рабочая роль не привязывает

- **WHEN** `HARNESS_ROLE=worker` или `guard-scope.role=worker`
- **THEN** команда отказывает, файл не создаётся; env не перебивает scope

### Requirement: [REQ-ATTACH-2] Плагин диспетчит сессию один раз

При `agent/created` плагин ДОЛЖЕН (SHALL), если привязка есть, агент — primary
(не субагент) и scope пуст, вызвать `harness dispatch --task <id> --primary-agent
<id>`. Dispatch допустим из BRIEF_READY и REWORK_READY (во втором случае он
резервирует попытку); при реальном отказе dispatch причина пишется в
`.harness/session-attach.error`. После успеха привязка удаляется. Чужой активный
scope не перезаписывается; привязка при конфликте сохраняется для ручного разбора.

#### Scenario: [CASE-ATTACH-2-1] Автодиспетч

- **WHEN** есть привязка T, создаётся primary A, scope пуст, задача BRIEF_READY
- **THEN** вызывается dispatch с `--primary-agent A`; задача становится RUNNING,
  change — IN_PROGRESS, привязка удаляется

#### Scenario: [CASE-ATTACH-2-2] Повторный агент

- **WHEN** scope уже содержит task_id=T
- **THEN** dispatch повторно не вызывается, привязка не трогается

#### Scenario: [CASE-ATTACH-2-3] После завершения задачи

- **WHEN** привязка уже была использована и scope снят вердиктом
- **THEN** нового dispatch нет: привязка удалена после успеха

#### Scenario: [CASE-ATTACH-2-4] Чужой активный scope

- **WHEN** scope относится к другой задаче
- **THEN** dispatch не вызывается, в `.harness/session-attach.error` записан конфликт,
  привязка остаётся для ручного разбора

#### Scenario: [CASE-ATTACH-2-5] Ошибка dispatch

- **WHEN** dispatch завершился ошибкой или spawn/timeout
- **THEN** причина записана в `.harness/session-attach.error`, сессия не падает

#### Scenario: [CASE-ATTACH-2-6] Субагент не становится primary

- **WHEN** `agent/created` приходит для субагента
- **THEN** `subagents.rootOf(agent)` не равен агенту, dispatch не вызывается

#### Scenario: [CASE-ATTACH-2-7] Повторный dispatch из REWORK_READY

- **WHEN** задача в REWORK_READY, есть attach, scope пуст
- **THEN** dispatch успешен, задача → RUNNING, попытка резервируется

### Requirement: [REQ-ATTACH-3] Отвязка и границы

`harness session detach` удаляет файл. Вердикт, commit и accept не автоматизируются;
attach — control-plane вход, shell-запись в `.harness` запрещена.

#### Scenario: [CASE-ATTACH-3-1] Detach

- **WHEN** выполнена `session detach`
- **THEN** `session-attach.json` отсутствует

#### Scenario: [CASE-ATTACH-3-2] Нет автоматических гейтов

- **WHEN** проверяется регистрация плагина
- **THEN** есть только dispatch по attach и read-only status; verdict/commit/accept
  не регистрируются

#### Scenario: [CASE-ATTACH-3-3] Shell не пишет control-plane

- **WHEN** worker выполняет `echo x > .harness/session-attach.json` или
  `echo x > policies/policy.json`
- **THEN** Python-хук и JS-guard дают R-PROT-1 (parity-кейс)
