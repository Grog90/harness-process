# Spec Delta

## Purpose

Закрыть остатки после public: live-панель, публичная гигиена, независимая проверка
подписи.

## ADDED Requirements

### Requirement: [REQ-PANEL-LIVE-1] Same-origin данные

Панель ДОЛЖНА (SHALL) читать `/harness-panel/metrics.json` same-origin; host-часть
проксирует к `HARNESS_BOARD_URL` (по умолчанию `http://127.0.0.1:8765/metrics.json`).

#### Scenario: [CASE-PANEL-LIVE-1-1] Доска есть

- **WHEN** board запущен
- **THEN** route отдаёт JSON, панель показывает live changes/tasks

#### Scenario: [CASE-PANEL-LIVE-1-2] Доски нет

- **WHEN** board недоступен
- **THEN** route отдаёт 502, панель показывает offline

### Requirement: [REQ-PUBLIC-1] Гигиена public

Публичный репозиторий ДОЛЖЕН (SHALL) иметь `SECURITY.md`, `LICENSE`, включённые
secret scanning и push protection.

#### Scenario: [CASE-PUBLIC-1-1] Файлы и сканер

- **WHEN** проверяется репозиторий
- **THEN** файлы есть, secret scanning включён

### Requirement: [REQ-ATTEST-1] Независимая проверка подписи

`harness attest` ДОЛЖЕН (SHALL) проверять `git verify-commit` по allowed_signers,
не доверяя состоянию harness; неподписанный коммит — отказ.

#### Scenario: [CASE-ATTEST-1-1] Подпись есть

- **WHEN** HEAD подписан доверенным ключом
- **THEN** `ok=true`

#### Scenario: [CASE-ATTEST-1-2] Подписи нет

- **WHEN** коммит не подписан
- **THEN** `ok=false`, `R-ATTEST-1`

### Requirement: [REQ-FINALIZER-DOC-1] Границы доверия

`docs/FINALIZER.md` ДОЛЖЕН (SHALL) разделять tamper-evident и tamper-proof и
перечислять, что нужно для второго.

#### Scenario: [CASE-FINALIZER-DOC-1-1] Документ

- **WHEN** читается docs/FINALIZER.md
- **THEN** есть оба термина и внешний процесс/HSM
