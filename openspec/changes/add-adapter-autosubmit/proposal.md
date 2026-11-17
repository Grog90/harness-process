# Предложение: opt-in авто-submit из DSH-сессии

## Problem

`session attach` автоматизирует dispatch, но submit остаётся ручным CLI. Для
коротких задач оператор хочет, чтобы сессия сама отправила результат на проверку,
не отдавая агенту verdict/commit/accept.

## What Changes

- `harness session attach --auto-submit` пишет `.harness/session-auto-submit.json`;
- плагин на `agent/turn-stopping` один раз вызывает `harness submit --task --attempt`;
- маркер снимается после попытки, ошибка пишется в `.harness/session-attach.error`;
- авто-submit работает только для корневого агента и активной задачи; verdict,
  commit и accept не автоматизируются.

## Impact

- Класс риска: **L1** (задача переходит в VERIFYING без отдельного CLI-действия).
- Только явный opt-in; по умолчанию поведение прежнее.
- Риск: первый turn может быть незавершённым — opt-in предназначен для коротких
  задач/явного решения оператора.

## Out of Scope

- Автоматический verdict, commit, accept.
- Повторный auto-submit после ошибки.
