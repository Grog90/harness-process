# Spec Delta

## Purpose

Закрыть остаток мультикорневой записи: кандидат и финализатор должны видеть и
фиксировать не только главный репозиторий, но и явно открытые сервисы.

## ADDED Requirements

### Requirement: [REQ-COMMIT-1] Снимок задачи включает writable-сервисы

Система ДОЛЖНА (SHALL) считать `candidate_snapshot` как hash главного дерева и
деревьев сервисов с `write=true`; read-only сервисы в снимок не входят.

#### Scenario: [CASE-COMMIT-1-1] Правка сервиса меняет снимок

- **WHEN** сервис `api` открыт write=true, файл `service:api/src/a.py` изменён
- **THEN** `snapshot_for_task` возвращает другой hash, чем до правки

#### Scenario: [CASE-COMMIT-1-2] Read-only сервис не входит

- **WHEN** сервис объявлен write=false, его файл изменён
- **THEN** снимок задачи не меняется (правка всё равно запрещена policy)

### Requirement: [REQ-COMMIT-2] Finalize коммитит сервисы с write-set-проверкой

Перед главным коммитом финализатор ДОЛЖЕН (MUST) проверить изменённые файлы каждого
writable-сервиса по `allowed_files`, закоммитить сервисный репозиторий и записать SHA.

#### Scenario: [CASE-COMMIT-2-1] Сервисный файл коммитится

- **WHEN** в сервисе изменён `service:api/src/a.py`, файл разрешён брифом
- **THEN** создаётся коммит в сервисном репозитории, SHA возвращается в `service_commits`

#### Scenario: [CASE-COMMIT-2-2] Посторонний файл сервиса блокирует

- **WHEN** в сервисе изменён файл вне `allowed_files`
- **THEN** финализация отклоняется с R-WRITESET-1, главный коммит не создаётся
  (ранее закоммиченные сервисы — возможный частичный результат, см. design Risks)

#### Scenario: [CASE-COMMIT-2-3] In-root сервис без своего git

- **WHEN** сервис лежит внутри главного корня и не является отдельным git-репозиторием
- **THEN** его файлы коммитятся главным коммитом, `service_commits` содержит `mode=main`

### Requirement: [REQ-COMMIT-3] Сервисные коммиты попадают в evidence

Пакет evidence ДОЛЖЕН (SHALL) содержать коммиты задач, включая сервисные SHA.

#### Scenario: [CASE-COMMIT-3-1] SHA сервиса в манифесте

- **WHEN** задача закоммитила сервис и change закрывается
- **THEN** `manifest.json` содержит `task_commits` с этим SHA
