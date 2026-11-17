# Предложение: статус PR и checks через forge

## Problem

`lane pr` создаёт PR, но harness не умеет прочитать его статус: упали ли checks,
идут ли ещё, есть ли конфликт. Оператор узнаёт это глазами в GitHub; CI остаётся
внешним и не связан с приёмкой.

## What Changes

- `harness/forge.py`: `pr_status`, `wait_status`, `summarize_checks`, отказ без `gh`;
- CLI `harness forge status --task T [--wait --timeout]`;
- статус сводится к `ok/failing/pending/mergeable/reviewDecision`;
- `--wait` ждёт завершения checks и отказывает по таймауту.

## Impact

- Класс риска: **L1** (read-only, но влияет на решение «можно принимать»).
- Требует `gh` и авторизации; без них — отказ, не «зелено».
- `gh` читает PR, но не мержит и не меняет настройки.

## Out of Scope

- Auto-merge и required checks (branch protection недоступна на free-private).
- Другие forge (GitLab/Bitbucket) — интерфейс назван GitHub, расширение отдельно.
