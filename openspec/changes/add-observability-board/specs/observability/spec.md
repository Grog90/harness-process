# Spec Delta

## Purpose

Видимость процесса без чтения логов: метрики из состояния, доска изменений и
CI-снимок здоровья.

## ADDED Requirements

### Requirement: [REQ-OBS-1] Метрики считаются из состояния

Система ДОЛЖНА (SHALL) считать метрики изменений, задач, review, verification,
evidence, lead time и rework из SQLite и файлов change, не заводя отдельного
отчётного хранилища.

#### Scenario: [CASE-OBS-1-1] Lead time Ready → Accept

- **WHEN** у change есть события ready и accept
- **THEN** `harness metrics` показывает ненулевой lead time и число выборок

#### Scenario: [CASE-OBS-1-2] Нулевой verification виден

- **WHEN** verification.json содержит прогон с 0 тестов
- **THEN** метрика verification помечает его как проблемный

### Requirement: [REQ-BOARD-1] Доска показывает очередь и гейты

Система ДОЛЖНА (SHALL) генерировать самодостаточный HTML со состояниями change,
review/trace/verification, release/accept и очередью; данные экранируются.

#### Scenario: [CASE-BOARD-1-1] HTML экранирует владельца

- **WHEN** владелец change содержит `<script>`
- **THEN** в HTML нет исполняемого тега, есть экранированный текст

### Requirement: [REQ-CI-1] CI-снимок пишет evidence-отчёт

Система ДОЛЖНА (SHALL) по `harness ci` собрать selftest, покрытие, метрики и
опционально unittest в `evidence/ci-<ts>/report.json`, возвращая ненулевой код при
провале.

#### Scenario: [CASE-CI-1-1] Отчёт создаётся

- **WHEN** запущен `harness ci --report ...`
- **THEN** JSON содержит selftest, coverage, metrics_summary и git_head

### Requirement: [REQ-OBS-2] Живая доска обновляет данные на запрос

Система ДОЛЖНА (SHALL) по `harness board --serve` отдавать `/` (HTML с
автообновлением), `/metrics.json` и `/healthz`, перечитывая состояние на каждый запрос.

#### Scenario: [CASE-OBS-2-1] Health и JSON

- **WHEN** запрошены `/healthz` и `/metrics.json`
- **THEN** приходят 200, `ok` и актуальные метрики; HTML содержит refresh

### Requirement: [REQ-OBS-3] OTLP/HTTP JSON с метриками процесса

Система ДОЛЖНА (SHALL) по `harness otel` собрать OTLP JSON (`resourceMetrics` →
`scopeMetrics` → gauge) с метриками harness, записать в файл или отправить POST.

#### Scenario: [CASE-OBS-3-1] Форма OTLP

- **WHEN** собран OTLP payload
- **THEN** ресурс содержит service.name/version, а метрики — asInt/asDouble

### Requirement: [REQ-CI-2] Headless-точка запуска проверяется smoke-скриптом

Система ДОЛЖНА (SHALL) запускать `dsh headless --json` через
`tools/headless_smoke.py`, писать session/final reason в evidence и возвращать
ненулевой код при final с ошибкой.

#### Scenario: [CASE-CI-2-1] Нет ключа — это FAIL с причиной

- **WHEN** headless вернул `MISSING_CREDENTIAL`
- **THEN** отчёт содержит final reason и exit 2, а не выдаёт это за успех
