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

#### Scenario: [CASE-WRITE-1-2] Флаг открывает сервис

- **WHEN** change создан с `--service api=PATH --service-write api`
- **THEN** в services пишется `write=true`, и бриф с `service:api/src/a.py` валиден

### Requirement: [REQ-WRITE-2] Запись только в allowed_files сервиса

Worker ДОЛЖЕН (MUST) писать в сервис только если ключ
`service:<name>/<relative>` есть в `allowed_files`; путь внутри сервиса не содержит
`..` и не абсолютный.

#### Scenario: [CASE-WRITE-2-1] Другой файл сервиса

- **WHEN** allowed_files содержит `service:api/src/a.py`, а агент пишет `service:api/src/b.py`
- **THEN** отказ R-WORK-1

#### Scenario: [CASE-WRITE-2-2] Разрешённый файл

- **WHEN** агент пишет `service:api/src/a.py`, сервис открыт, роль worker
- **THEN** запись разрешена и файл появляется в сервисном корне

### Requirement: [REQ-WRITE-3] Побег из сервисного корня запрещён

Guard и gateway ДОЛЖНЫ (MUST) отклонять путь вне объявленного сервиса, сервис без
`write=true`, роль без `can_write_services`, symlink/junction и `..`; shell-запись в
сервис не открывается.

#### Scenario: [CASE-WRITE-3-1] Чужой корень

- **WHEN** агент пишет `../other/src/a.py`, а объявлен только `api=../svc`
- **THEN** отказ R-PROT-1

#### Scenario: [CASE-WRITE-3-2] Verifier не пишет сервис

- **WHEN** verifier пишет `service:api/src/a.py` при write=true
- **THEN** отказ R-VER-1

#### Scenario: [CASE-WRITE-3-3] Read-only сервис

- **WHEN** сервис объявлен с write=false, а агент пишет `service:api/src/a.py`
- **THEN** отказ R-PROT-1

#### Scenario: [CASE-WRITE-3-4] Symlink/junction наружу

- **WHEN** внутри сервиса есть ссылка на каталог вне сервисного корня
- **THEN** gateway отклоняет запись по этому пути с R-PROT-1

#### Scenario: [CASE-WRITE-3-5] Shell-запись

- **WHEN** агент делает `echo x >> ../svc/config.yaml` или `>> services/api/config.yaml`
- **THEN** hook отклоняет команду с R-PROT-1

#### Scenario: [CASE-WRITE-3-6] Сервис внутри главного корня

- **WHEN** сервисный корень лежит внутри проекта и путь попадает в `policies/**`
- **THEN** service-запись отклоняется с R-PROT-1, как обычный protected-путь

### Requirement: [REQ-WRITE-4] Права сервиса берутся из change, а не из брифа

Система ДОЛЖНА (SHALL) сверять services брифа с change при диспетче; бриф не может
открыть write=true для сервиса, который change не открывал, или подменить путь.

#### Scenario: [CASE-WRITE-4-1] Бриф пытается открыть сервис

- **WHEN** change объявил сервис write=false, а бриф — write=true
- **THEN** диспетч отклоняется с R-CHG-1

#### Scenario: [CASE-WRITE-4-2] Сервис содержит главный корень

- **WHEN** сервисный root — предок проекта (`path=..`)
- **THEN** `change new` отклоняет такую декларацию, а policy всё равно проверяет target по главному пути

## Out of Scope

Snapshot/commit/finalize сервисных репозиториев остаются вне v1.
