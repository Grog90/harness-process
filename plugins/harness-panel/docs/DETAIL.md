# Harness panel

Панель в композере DSH показывает сводку harness: изменения, активные задачи,
rework rate. Данные читаются с локальной доски `harness board --serve` по
`http://127.0.0.1:8765/metrics.json`; в `localStorage.harnessBoardUrl` задаётся
**полный URL** JSON-метрик (не базовый адрес доски).

Без запущенной доски панель показывает `доска offline` и команду запуска. Это
read-only вид: гейты и приёмка остаются в harness, панель ничего не меняет.
