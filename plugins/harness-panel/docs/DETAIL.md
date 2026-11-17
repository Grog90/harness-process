# Harness panel

Панель в композере DSH показывает сводку harness: изменения, активные задачи,
rework rate. Данные читаются с локальной доски `harness board --serve`; клиент
запрашивает same-origin `/harness-panel/metrics.json`, а host-часть плагина
проксирует его к `http://127.0.0.1:8765/metrics.json`. Адрес доски для host-части
переопределяется `HARNESS_BOARD_URL`; клиентского override нет.

Без запущенной доски панель показывает `доска offline` и команду запуска. Это
read-only вид: гейты и приёмка остаются в harness, панель ничего не меняет.
