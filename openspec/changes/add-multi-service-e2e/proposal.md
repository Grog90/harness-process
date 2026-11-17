# Предложение: multi-service e2e

## Problem

Merge order записывается и сверяется, но интеграционного прогона нескольких
сервисов нет: нельзя проверить, что api и web совместимы до commit/accept.

## What Changes

- `harness e2e run --change C --command CMD --service name=PATH[@SHA]...`;
- каждый сервис копируется во временный каталог (без .git/node_modules), рабочее
  дерево не меняется;
- команда получает `HARNESS_E2E_<NAME>_DIR` и `_SHA`;
- отчёт `evidence/e2e-<change>.json`: команда, SHA, exit, duration, tail stdout/stderr;
- прогон требует `may_commit` (оператор/оркестратор).

## Impact

- Класс риска: **L1** (исполнение внешней команды; копии, не рабочие деревья).
- Ограничение: копия без зависимостей; e2e — smoke, а не полноценный стенд.
- Отчёт не подменяет verification: он дополняет её для нескольких сервисов.

## Out of Scope

- Поднятие Docker/БД/сетевого стенда.
- Автоматический запуск e2e из `change ready` (гейт отдельным решением).
- Merge order как очередь: e2e не мержит PR.
