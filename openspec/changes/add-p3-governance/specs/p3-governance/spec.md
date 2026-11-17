# Spec Delta

## Purpose

Сделать PII/SLO/legal проверяемыми, а не декларативными.

## ADDED Requirements

### Requirement: [REQ-PII-1] Поиск и редакция PII

`pii.scan/redact` ДОЛЖНЫ (SHALL) находить email, телефон, карту, IBAN, JWT и
приватный ключ и заменять их на `[REDACTED:<класс>]`. CLI без `--text/--file` —
отказ; `redact --out` не перезаписывает исходный файл.

#### Scenario: [CASE-PII-1-1] Скан

- **WHEN** текст содержит email/телефон/карту/JWT
- **THEN** счётчики по классам > 0

#### Scenario: [CASE-PII-1-2] Редакция

- **WHEN** текст редактируется
- **THEN** исходные значения отсутствуют, маркеры присутствуют

### Requirement: [REQ-SLO-1] Пороговый отчёт

`slo.evaluate` ДОЛЖЕН (SHALL) возвращать строки значение/цель/вердикт и флаг
`confirmed`; пустая выборка — `ok=null` (`n/a`), а не успех; `--strict` — exit 2
при нарушении.

#### Scenario: [CASE-SLO-1-1] Нарушение

- **WHEN** acceptance_rate ниже порога
- **THEN** `ok=false`, имя в `breached`

#### Scenario: [CASE-SLO-1-2] Нет данных

- **WHEN** метрики пусты
- **THEN** строки `n/a`, `ok=true`, `--strict` не падает

### Requirement: [REQ-LEGAL-1] Инвентарь данных

`docs/LEGAL.md` ДОЛЖЕН (SHALL) перечислять, какие данные хранятся, где, как
вывозятся (включая DSH → LLM-провайдер), что с PII и чего не сделано.

#### Scenario: [CASE-LEGAL-1-1] Документ есть

- **WHEN** проверяется docs/LEGAL.md
- **THEN** есть разделы данные/PII/хранение/лицензии/границы

### Requirement: [REQ-EVAL-1] Evals остаются отдельным контролем

SLO ДОЛЖЕН (MUST) не подменять `harness evals`; docs/SLO.md фиксирует разницу.

#### Scenario: [CASE-EVAL-1-1] Документ

- **WHEN** читается docs/SLO.md
- **THEN** evals описаны как отдельный контроль
