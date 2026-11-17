# Spec Delta

## Purpose

Сделать DSH-сессию задачей harness без ручного `harness dispatch`, не отдавая
агенту вердикт, commit и приёмку.

## ADDED Requirements

### Requirement: [REQ-ATTACH-1] Привязка пишется явной командой

`harness session attach --task <id>` ДОЛЖЕН (MUST) проверить существование задачи и
записать `.harness/session-attach.json` с `task_id`, `auto_dispatch=true` и временем.

#### Scenario: [CASE-ATTACH-1-1] Привязка существующей задачи

- **WHEN** задача зарегистрирована и выполнена `session attach --task T`
- **THEN** файл содержит `task_id=T`, `auto_dispatch=true`

#### Scenario: [CASE-ATTACH-1-2] Неизвестная задача

- **WHEN** `session attach --task missing`
- **THEN** команда завершается ошибкой и файл не создаётся

### Requirement: [REQ-ATTACH-2] Плагин диспетчит сессию один раз

При `agent/created` плагин ДОЛЖЕН (SHALL), если привязка есть и scope ещё не
относится к этой задаче, вызвать `harness dispatch --task <id> --primary-agent <id>`;
ошибка пишется в `.harness/session-attach.error` и не роняет сессию.

#### Scenario: [CASE-ATTACH-2-1] Автодиспетч

- **WHEN** есть привязка T, создаётся агент A, scope пуст
- **THEN** вызывается dispatch с `--primary-agent A`, задача становится IN_PROGRESS

#### Scenario: [CASE-ATTACH-2-2] Повторный агент

- **WHEN** scope уже содержит task_id=T
- **THEN** dispatch повторно не вызывается

### Requirement: [REQ-ATTACH-3] Отвязка и границы

`harness session detach` удаляет файл. Вердикт, commit и accept не автоматизируются.

#### Scenario: [CASE-ATTACH-3-1] Detach

- **WHEN** выполнена `session detach`
- **THEN** `session-attach.json` отсутствует
