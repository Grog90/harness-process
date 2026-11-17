# Предложение: панель harness в DSH GUI

## Problem

Живая доска harness — отдельный локальный HTTP-сервер и HTML вне DSH. Оператор
хочет видеть изменения/задачи в самом интерфейсе, не переключаясь в браузер.

## What Changes

- client-плагин `@local/harness-panel` добавляет панель в `conversation.composer.dock`;
- панель читает `http://127.0.0.1:8765/metrics.json` (адрес переопределяется
  `localStorage.harnessBoardUrl`), обновляется раз в 15 с;
- показывает сводку, 4 последних change и активные задачи; при недоступной доске —
  `доска offline` и команду запуска;
- сервер доски отдаёт `Access-Control-Allow-Origin: *`, иначе fetch из GUI режется CORS.

## Impact

- Класс риска: **L1** (read-only вид; гейты не меняются).
- Плагин ставится в профиль `desktop`, влияет на все сессии профиля.
- Без `harness board --serve` панель показывает offline, а не тихо пусто.

## Out of Scope

- Управление change из панели (accept/release) — гейты остаются в CLI.
- WebSocket/SSE: обновление polling раз в 15 с.
- Замена доски: HTML-доска остаётся для headless/браузера.
