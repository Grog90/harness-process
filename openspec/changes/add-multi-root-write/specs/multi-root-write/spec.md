# Spec Delta

## Purpose

Разрешить worker'у писать в явно открытые сервисные репозитории, не ослабляя
однокорневую защиту для всех остальных.

## ADDED Requirements

### Requirement: [REQ-WRITE-1] Сервис открывается для записи явно

Change ДОЛЖЕН (MUST) помечать сервис `write=true` только через
`change new --service-write NAME`; без этого сервис read-only.

#### Scenario: [CASE-WRITE-1-1] Без флага запись запрещена

- **WHEN** сервис объявлен без `--service-write`, а бриф разрешает `service:api/src/a.py`
- **THEN** brief.validate отклоняет allowed_files (E_CONTRADICTION)

### Requirement: [REQ-WRITE-2] Запись только в allowed_files сервиса

Worker ДОЛЖЕН (MUST) писать в сервис только если ключ
`service:<name>/<relative>` есть в `allowed_files`; путь внутри сервиса не содержит
`..` и не абсолютный.

#### Scenario: [CASE-WRITE-2-1] Другой файл сервиса

- **WHEN** allowed_files содержит `service:api/src/a.py`, а агент пишет `src/b.py`
- **THEN** отказ R-WORK-1

#### Scenario: [CASE-WRITE-2-2] Разрешённый файл

- **WHEN** агент пишет `service:api/src/a.py`, сервис открыт, роль worker
- **THEN** запись разрешена и файл появляется в сервисном корне

### Requirement: [REQ-WRITE-3] Побег из сервисного корня запрещён

Guard и gateway ДОЛЖНЫ (MUST) отклонять путь вне объявленного сервиса, сервис без
`write=true`, роль без `can_write_services`, symlink/`..`; shell-запись в сервис не
открывается.

#### Scenario: [CASE-WRITE-3-1] Чужой корень

- **WHEN** агент пишет `../other/src/a.py`, а объявлен только `api=../svc`
- **THEN** отказ R-PROT-1

#### Scenario: [CASE-WRITE-3-2] Verifier не пишет сервис

- **WHEN** verifier пишет `service:api/src/a.py` при write=true
- **THEN** отказ R-PROT-1

## Out of Scope

Snapshot/commit/finalize сервисных репозиториев остаются вне v1.
