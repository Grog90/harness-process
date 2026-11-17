# finalizer-signing Specification

## Purpose

Не дать финализатору и merge-очереди создать неподписанный коммит молча.

## Requirements

### Requirement: [REQ-FSIGN-1] Preflight подписи

Если в репозитории `commit.gpgsign=true` или `HARNESS_REQUIRE_SIGNED=1`,
финализатор и merge ДОЛЖНЫ (SHALL) отказать `R-FINAL-2` до коммита, когда нет
`user.signingkey`.

#### Scenario: [CASE-FSIGN-1-1] Нет ключа

- **WHEN** `commit.gpgsign=true`, `user.signingkey` пуст
- **THEN** `R-FINAL-2`, ни один коммит не создан

#### Scenario: [CASE-FSIGN-1-2] Подпись не требуется

- **WHEN** локальный конфиг не включает подпись
- **THEN** поведение прежнее, коммит без `-S`

### Requirement: [REQ-FSIGN-2] Проверка после коммита

Если подпись требуется (см. REQ-FSIGN-1), после коммита main/сервиса/merge
ДОЛЖЕН (SHALL) выполняться `git verify-commit`; неподписанный или чужой коммит —
`R-FINAL-2` с откатом.

#### Scenario: [CASE-FSIGN-2-1] Main

- **WHEN** main-коммит создан
- **THEN** `verify-commit HEAD` проходит, иначе откат

#### Scenario: [CASE-FSIGN-2-2] Сервис

- **WHEN** сервисный коммит создан
- **THEN** `verify-commit` в сервисе проходит, иначе откат всех сервисных

### Requirement: [REQ-FSIGN-3] Merge и сервисы

`lanes.merge` и `commit_services` ДОЛЖНЫ (SHALL) использовать контекст подписи
основного репозитория, включая `gpg.format`, `user.signingkey` и
`allowedSignersFile`.

#### Scenario: [CASE-FSIGN-3-1] Merge

- **WHEN** подпись требуется
- **THEN** merge-коммит создаётся с `-S` и проверяется

### Requirement: [REQ-FSIGN-4] Документация

`docs/SIGNING.md` ДОЛЖЕН (SHALL) описывать финализатор, сервисы, merge и
`HARNESS_REQUIRE_SIGNED`.

#### Scenario: [CASE-FSIGN-4-1] Документ

- **WHEN** читается docs/SIGNING.md
- **THEN** есть раздел про финализатор и env
