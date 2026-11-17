# Предложение: автопривязка DSH-сессии к задаче

## Problem

Плагин `harness-session` показывает задачу в контексте, но диспетч всё ещё делается
вручную: сессия не становится задачей, пока оператор не выполнит `harness dispatch`.

## What Changes

- `harness session attach --task <id>` пишет `.harness/session-attach.json`;
- `harness session status|detach` показывает/снимает привязку;
- плагин при `agent/created` один раз вызывает
  `harness dispatch --task <id> --primary-agent <agent>`; после успеха привязка
  удаляется, повторного dispatch нет;
- чужой активный scope не перезаписывается, причина пишется в
  `.harness/session-attach.error`;
- ошибка диспетча пишется в `.harness/session-attach.error` и не роняет сессию;
- вердикт, commit и приёмка не автоматизируются.

## Impact

- Класс риска: **L1** (задача переходит в RUNNING, change — в IN_PROGRESS;
  резервируется попытка).
- Привязку делает оператор/человек; CLI отказывает worker/verifier/analyst/qa,
  detach разрешён всем (иначе конфликтную привязку не снять).
- Запрет shell-записи в `.harness`/protected добавлен в `harness/hooks.py` и
  `plugins/harness-guard/guard-core.mjs`, parity-кейсы — в `tests/test_parity.py`.
- Новые `harness/session.py`, CLI-команда, правки плагина, тесты.
- Требует задачи в BRIEF_READY или REWORK_READY; иначе dispatch откажет, причина
  попадёт в error-файл.

## Out of Scope

- Автоматический verdict/commit/accept.
- Привязка нескольких задач к одной сессии.
