# session-adapter Specification

## Purpose

Связать активную задачу harness с DSH-сессией: роль и границы видны в контексте,
статус читается инструментом, гейты не отдаются агенту.

## Requirements

### Requirement: [REQ-ADAPT-1] Активная задача видна в контексте сессии

Плагин ДОЛЖЕН (MUST) добавлять в системный контекст task_id, роль, allowed_files,
сервисы, фазу и попытку из `.harness/guard-scope.json`, если файл существует.

#### Scenario: [CASE-ADAPT-1-1] Задача активна

- **WHEN** `.harness/guard-scope.json` содержит task_id и role=worker
- **THEN** контекст содержит task_id, `worker`, allowed_files и фазу

#### Scenario: [CASE-ADAPT-1-2] Задачи нет

- **WHEN** файла области нет
- **THEN** блок контекста пуст, регистрация провайдера не падает

### Requirement: [REQ-ADAPT-2] Статус задачи доступен инструментом

Плагин ДОЛЖЕН (SHALL) регистрировать read-only инструмент `harness_task`, который
возвращает состояние активной задачи и её последний вердикт.

#### Scenario: [CASE-ADAPT-2-1] Статус по активной задаче

- **WHEN** агент вызывает `harness_task` без аргументов при активной задаче
- **THEN** инструмент возвращает `{ok:true, status:<JSON harness task status>}` для её task_id

#### Scenario: [CASE-ADAPT-2-2] Нет активной задачи

- **WHEN** области нет, а task_id не передан
- **THEN** инструмент возвращает `{ok:false, error}` с понятной причиной, а не пустой статус

### Requirement: [REQ-ADAPT-3] Плагин не расширяет права

Плагин НЕ ДОЛЖЕН (MUST NOT) диспетчить, выносить вердикт, коммитить или писать
файлы продукта; он читает scope и вызывает read-only `task status` (тот может
создать/мигрировать `.harness/state.db`, но не меняет состояние задачи).

#### Scenario: [CASE-ADAPT-3-1] Нет мутирующих инструментов

- **WHEN** проверяется схема инструмента и регистрации плагина
- **THEN** есть единственный инструмент `harness_task` с аргументом только `task_id`;
  dispatch/verdict/commit не зарегистрированы
