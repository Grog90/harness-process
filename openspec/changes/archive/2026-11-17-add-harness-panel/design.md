# Design: панель harness

## Decisions

**1. Слот `conversation.composer.dock`.**
Он уже выделяет место под композером и не перекрывает чат; регистрация с своим
id `harness-panel`, order 20. Встроенные `activity`/`usage` не заменяются.

**2. Источник данных — `/metrics.json` доски.**
Панель делает `fetch` раз в 15 с; URL по умолчанию
`http://127.0.0.1:8765/metrics.json`, переопределяется
`localStorage.harnessBoardUrl`. Сервер доски отдаёт `Access-Control-Allow-Origin: *`,
иначе браузер режет запрос. `rework_rate` берётся из `attempts`, а не `summary`;
changes сортируются по `created_at` (свежие сверху), активные задачи — по
состояниям RUNNING/VERIFYING/VERIFY_BLOCKED/REWORK_READY (как `TASK_ACTIVE`).

**3. Offline честный.**
Ошибка fetch показывается как `доска offline` и команда запуска; при отсутствии
данных — `нет данных`, а не нули. Если данные уже были, показывается `stale`.
Мутаций нет: только чтение и локальный refresh.

## Risks

- Нужен запущенный `harness board --serve`; без него панель offline.
- Polling 15 с, а не SSE; данные могут отставать.
- CORS `*` у локального сервера: metrics может прочитать любой процесс на машине
  и любая веб-страница, которая сделает cross-origin fetch к 127.0.0.1; при
  `--host 0.0.0.0` — ещё и по сети.
- Визуальная проверка в GUI требует авторизации; Inspect подтвердил регистрацию
  occupant, но не пиксели/рендер.
