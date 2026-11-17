# Предложение: исполняемые can_read и may_*

## Problem

`policies/policy.json` описывает `can_read` и `may_dispatch/may_commit/may_verify`,
но код их не проверяет:
- воркер с `bash` может вызвать `harness dispatch`, `harness commit` или
  `harness verdict` и обойти роли;
- `can_read` остаётся документацией: чтение не ограничено ни Python-хуком, ни
  JS-guard.

Это дыра в границах, а не косметика: роль заявлена, но не исполняется.

## What Changes

- `Policy.check_capability` и проверка в `cmd_dispatch/cmd_commit/cmd_verdict/cmd_finalize`;
- `Policy.check_read` и хук `policy.read_scope` для `read_file/glob/grep`;
- та же проверка в JS-guard (DSH-сессии);
- тесты: политика, хук, CLI-отказ роли worker.

## Impact

- Класс риска: **L1** (может отказать существующему сценарию, если роль вызывала
  команду без права).
- `human`/`system`/`orchestrator` сохраняют права; worker/verifier ограничены.
- `can_read` сейчас `**` у всех, поэтому поведение не меняется до ужесточения
  политики; механизм становится исполняемым.

## Out of Scope

- Доверенная идентичность (подмена `guard-scope`/env) — отдельный пункт.
- Ограничение чтения через `bash`/`python -c` — эвристика, отдельный риск.
