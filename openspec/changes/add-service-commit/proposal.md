# Предложение: коммит и tree-snapshot сервисов

## Problem

После `add-multi-root-write` worker может писать в явно открытый сервис, но:

- verification-снимок считается только по главному корню, поэтому правка сервисного
  файла не меняет `candidate_snapshot` и не видна recheck;
- `finalize` проверяет write-set и коммитит только главный репозиторий; сервисные
  изменения остаются грязными и не попадают в evidence;
- `R-WRITESET-1` не видит чужие правки в сервисном репозитории.

## What Changes

- `snapshot.compute_tree` считает hash главного дерева и деревьев сервисов с
  `write=true`; `snapshot_for_task` — единая точка для submit/verify/finalize.
- `finalize` перед главным коммитом проверяет write-set каждого сервисного репозитория
  по `service:`-путям, коммитит его и возвращает SHA.
- `commits` в state получает записи сервисных коммитов; evidence добавляет их в
  манифест.
- Read-only сервисы в snapshot не входят; их правки по-прежнему запрещены policy.

## Impact

- Класс риска: **L1** (коммит в чужие репозитории).
- Меняются `harness/snapshot.py`, `harness/finalize.py`, `harness/fsm.py`,
  `harness/cli.py`, `harness/state.py`, `harness/changefsm.py`, тесты.
- Merge order и автоматический push не входят.

## Out of Scope

- Автоматический push/PR в сервисные репозитории.
- Разрешение конфликтов и исполнение merge order.
- Подпись коммитов.
