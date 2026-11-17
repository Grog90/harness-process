# Предложение: автопривязка DSH-сессии к задаче

## Problem

Плагин `harness-session` показывает задачу в контексте, но диспетч всё ещё делается
вручную: сессия не становится задачей, пока оператор не выполнит `harness dispatch`.

## What Changes

- `harness session attach --task <id>` пишет `.harness/session-attach.json`;
- плагин при `agent/created` видит файл и один раз вызывает
  `harness dispatch --task <id> --primary-agent <agent>`;
- ошибка диспетча пишется в `.harness/session-attach.error` и не роняет сессию;
- `harness session detach` убирает привязку;
- вердикт, commit и приёмка не автоматизируются.

## Impact

- Класс риска: **L1** (автоматический переход задачи в IN_PROGRESS).
- Новые `harness/session.py`, CLI-команда, правки плагина, тесты.
- Требует подготовленной задачи с брифом; иначе dispatch откажет.

## Out of Scope

- Автоматический verdict/commit/accept.
- Привязка нескольких задач к одной сессии.
