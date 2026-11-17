# Design: opt-in авто-submit

## Decisions

**1. Отдельный маркер.**
Attach удаляется после dispatch, поэтому opt-in живёт в
`.harness/session-auto-submit.json`. Маркер пишет CLI, плагин читает его в
`agent/turn-stopping` и удаляет после попытки. Без флага маркер удаляется при
attach/detach.

**2. Только корневой агент и один раз.**
`subagents.rootOf(agent) === agent`; scope.task_id должен совпадать с маркером;
attempt берётся из scope. После submit маркер удаляется, повторного нет даже при
ошибке — причина в error-файле.

**3. Граница.**
Автоматизируется только `harness submit` (задача → VERIFYING). Verdict остаётся
проверяющему, commit/accept — человеку. Auto-submit включается только явным
флагом и предназначен для коротких задач: первый turn может быть неполным.

## Risks

- Незавершённая работа может уйти в проверку; это цена opt-in.
- spawnSync блокирует закрытие turn до 30 с.
- Если scope attempt отсутствует, submit откажет; причина в error-файле.
