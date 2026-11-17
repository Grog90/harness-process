# Design: автопривязка сессии

## Decisions

**1. Файл привязки, а не env.**
`.harness/session-attach.json` переживает перезапуск DSH и читается плагином в
момент `agent/created`. Env-ветки для привязки нет; роль для CLI берётся сначала
из `guard-scope.json`, `HARNESS_ROLE` — только если scope отсутствует. Attach —
явный control-plane файл; shell-запись в `.harness` и protected запрещена и в
Python-хуке, и в JS-guard (parity-кейсы).

**2. Один диспетч на задачу.**
После успешного dispatch файл удаляется (`consumeAttach`), поэтому повторный
`agent/created` после снятия scope (вердикт/коммит/перезапуск) ничего не делает.
Если scope уже занят **другой** задачей, dispatch не вызывается: CLI перезаписал бы
чужие права, поэтому плагин пишет конфликт в error-файл и оставляет привязку
оператору для ручного разбора.

**3. Только primary.**
`agent/created` отдаёт создаваемого агента; основная проверка —
`ctx.subagents.rootOf(agent).id === agent.id`: субагент возвращает корень и не
становится primary. `agent.parent` — только fallback, если сервис `subagents`
недоступен; контракт `Agent` его не гарантирует, поэтому fallback назван риском.
`--primary-agent` — id корневого агента: guard даст ему orchestrator, дочерним —
роль фазы.

**4. Ошибка не роняет сессию.**
spawnSync с таймаутом 15 с; при `proc.error`, timeout, ненулевом exit или исключении
причина пишется в `.harness/session-attach.error`; успех удаляет старый error.
Сессия продолжается без привязки, причина видна оператору.

**5. Граница и актор.**
Attach — действие оператора: CLI отказывает роли worker/verifier/analyst/qa;
разрешены human/system/orchestrator. Detach ограничен теми же ролями.
Автоматизируется только dispatch (задача → RUNNING, change → IN_PROGRESS, резерв
попытки). Submit остаётся действием воркера, verdict — проверяющего, accept —
человека.

## Risks

- `agent/created` в общем профиле: один attach на корень; две одновременные задачи
  не поддерживаются.
- spawnSync блокирует создание агента до 15 с.
- Если задача не в BRIEF_READY, dispatch откажет; причина в error-файле.
- Attach-файл — control-plane input; защита держится на запрете shell-записи в
  `.harness` и отказе CLI для рабочих ролей.

