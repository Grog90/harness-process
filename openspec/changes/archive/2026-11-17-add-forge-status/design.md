# Design: forge status

## Decisions

**1. Только чтение.**
`gh pr list --head <branch> --json ...`; PR не создаётся и не мержится. Ветка —
`harness/<task>`, как у лейна.

**2. Сводка — чистая функция.**
`summarize_checks` отделяет failing (FAILURE/CANCELLED/TIMED_OUT/...) и pending
(QUEUED/IN_PROGRESS/...), чтобы это можно было тестировать без сети. `ok` — только
когда нет ни failing, ни pending.

**3. Ожидание с дедлайном.**
`wait_status` опрашивает с интервалом и падает по таймауту; бесконечного ожидания
нет. Ошибка `gh` — отказ, не пустой результат.

## Risks

- `gh` требует авторизации; в headless нужен токен.
- API GitHub может отдавать `statusCheckRollup` неполным; `pending` тогда честно
  не пуст.
- Branch protection по-прежнему не включена на free-private; статус читается, но
  merge технически не блокируется.
