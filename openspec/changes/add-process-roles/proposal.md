# Proposal: процессные роли, пресеты и guard Этапа 2

## Why

После Этапа 1 у изменения есть Ready-гейт, review и trace. Но роли BA/SA/QA
существовали только в документе: политика знала `worker`/`verifier`, диспетч всегда
публиковал роль `worker`, а guard не различал спеку и код. В итоге «BA не пишет код»
и «worker не правит спеку» держались на просьбе, а не на отказе.

Этап 2 делает роли исполняемыми: отдельные пресеты, роли политики `analyst`/`qa`,
привязка процессной роли из брифа к области прав, запрет привилегированных
инструментов рабочим и `ask` при открытом GAP.

## What Changes

- `policies/policy.json`: роли `analyst` (BA/SA) и `qa`, `spec_globs`,
  `privileged_tools`/`privileged_prefixes`, `mcp_allow_globs`.
- `harness/policy.py` + guard-core: R-WORK-3 (спеку пишут analyst/QA), R-TOOL-2
  (plugin_manager и опасные MCP закрыты рабочим), QA-исключение для `tests/**`.
- Бриф: `actor_role` (BA/SA/DEV/QA/verifier); диспетч публикует роль политики
  (`analyst`/`qa`/`worker`/`verifier`) в `guard-scope.json`.
- `harness/gap.py` + `harness gap ask|clear|show` + `tools/pre-execute` в guard'е:
  открытый вопрос превращает пишущий вызов в `ask` человеку.
- Пять процессных пресетов DSH (`plugins/process-presets`) и пять скиллов
  (`skills/process-*`).

## Impact

- Брифы без `actor_role` остаются `DEV` → `worker`, поведение совместимо.
- Новые правила R-WORK-3, R-TOOL-2, R-GAP-2; матрица 43 правила (40 наших).
- Установка пресетов меняет профиль DSH; права на пути по-прежнему определяет
  harness-guard, пресет лишь не выдаёт лишних инструментов.

## Out of Scope

- Приёмка/архив с evidence, baseline/candidate (Этап 3).
- Мультирепозиторность (Этап 5).
- UI-доска процесса.
