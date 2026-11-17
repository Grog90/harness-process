# CI и наблюдаемость

## GitHub Actions

Рабочий репозиторий: <https://github.com/Grog90/harness-process> (private).
Workflow `.github/workflows/ci.yml` на `windows-latest`:

1. `python -m unittest discover -s tests -q`;
2. `python -m harness ci --report evidence/ci/report.json` (selftest, coverage, metrics);
3. `python -m harness rule verify`;
4. cosign keyless подписывает `report.json` через GitHub OIDC;
5. подпись проверяется тем же workflow;
6. `evidence/ci` (отчёт + `.bundle`) уходит в artifact.

Branch protection на private-репозитории free-аккаунта GitHub недоступен (API 403:
нужен Pro или public). Поэтому зелёный `ci` пока обязателен по runbook, а не
технически; это названо в [`DEPLOY.md`](DEPLOY.md).

## Один отчёт вместо пяти логов

`harness ci` собирает снимок процесса и кода:

```powershell
python -m harness ci --run-tests
```

Он выполняет `selftest`, считает покрытие правил, снимает метрики из SQLite и
(с `--run-tests`) запускает `unittest`. Результат пишется в
`evidence/ci-<дата>/report.json`, код возврата — 0/2. Отчёт содержит версию Python,
Git HEAD, число правил/пробелов, selftest, метрики очереди и verification.

Пример для GitHub Actions:

```yaml
name: harness
on: [push, pull_request]
jobs:
  check:
    runs-on: windows-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.10"
      - run: python -m harness ci --run-tests
```

## Доска и метрики

```powershell
python -m harness metrics                  # markdown в stdout
python -m harness --json metrics           # машинный снимок
python -m harness metrics --markdown docs/status.md
python -m harness board --out board.html   # снимок HTML
python -m harness board --serve --port 8765 # живая доска по HTTP
```

`board --serve` перечитывает SQLite и файлы change на каждый запрос и отдаёт
`/` (HTML с автообновлением 15 с), `/metrics.json` и `/healthz`. Это отдельный
локальный сервер, а не встроенная панель DSH GUI. Доска показывает состояния
change, review, trace, verification, release/accept и очередь; HTML экранирует
данные, поэтому имя change/владельца не может исполнить скрипт.

## OTLP

```powershell
python -m harness otel --out evidence/otlp.json
python -m harness otel --endpoint http://127.0.0.1:4318/v1/metrics
```

Это OTLP/HTTP JSON (`resourceMetrics` → `scopeMetrics` → gauge), без protobuf/gRPC.
Метрики: `harness.changes`, `harness.tasks.active`, `harness.rework.rate`,
`harness.verification.ok`, `harness.findings.open` и т.п.; ресурс — `service.name`,
`service.version`, `host.name`, `vcs.revision`. Агентские трейсы экспортирует сам DSH.

## dsh-headless в CI

Headless-профиль установлен и запускается:

```powershell
dsh headless --json --permission-mode read-only --approval-policy never "Проверь ..."
```

Smoke-отчёт: `python tools/headless_smoke.py --out evidence/headless/report.json`.
Он пишет NDJSON-события, session id и final reason в evidence. В нашем окружении
прогон дошёл до `final`, но завершился `MISSING_CREDENTIAL`: у headless-профиля нет
`DEEPSEEK_API_KEY` (ключ, сохранённый в Web-профиле, CLI не видит). В CI задай
`DEEPSEEK_API_KEY` в окружении job — тогда final должен быть `end_turn`.

Пример CI с headless (плюс быстрый `harness ci`):

```yaml
- run: python -m harness ci --run-tests
- run: python tools/headless_smoke.py --task "Проверь, что репозиторий собран"
  env:
    DEEPSEEK_API_KEY: ${{ secrets.DEEPSEEK_API_KEY }}
```

## Что считается метрикой

| Метрика | Источник | Смысл |
|---|---|---|
| lead time Ready → Accept | `change_events` | сколько живёт изменение от готовности до приёмки |
| rework rate | `attempts` | доля задач, где понадобилась вторая попытка |
| review records | `change_reviews` | сколько независимых записей реально есть |
| verification ok / zero | `verification.json` | проверено ли изменение и не пустой ли прогон |
| evidence manifests | `evidence/change-*/manifest.json` | сколько изменений закрыто с пакетом |
| open findings | `findings` | что осталось без владельца/закрытия |

Цифры нельзя поправить отдельно от процесса: они читаются из тех же таблиц и
файлов, на которых стоят гейты.

## Границы

- `harness ci` — снимок кода и процесса, а не агентский прогон. Headless-путь
  проверен до `final`, но в этой установке упирается в отсутствие ключа; в CI нужен
  `DEEPSEEK_API_KEY`. Отчёт harness остаётся источником правды о гейтах.
- OTLP-экспорт harness — метрики процесса; агентские трейсы экспортирует DSH.
  Protobuf/gRPC и прямая встройка доски в DSH GUI не реализованы.
- Поиск по прошлым сессиям — инструмент DSH (`tool-session-query`), в профиле он
  отключён; это не часть harness.
- Доска не заменяет гейты: даже живой HTML не пропустит change без evidence.
- Headless использует отдельный профиль и отдельное окружение: ключ из Web-профиля
  не подхватывается автоматически.
