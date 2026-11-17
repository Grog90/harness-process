# Spec Delta

## Purpose

Публиковать лейн и не оставлять частично применённый финализатор.

## ADDED Requirements

### Requirement: [REQ-PUSH-1] Публикация ветки и PR

`harness lane push` ДОЛЖЕН (SHALL) пушить ветку лейна без force; `harness lane pr`
ДОЛЖЕН (SHALL) создавать PR через `gh` и отказывать, если `gh` нет.

#### Scenario: [CASE-PUSH-1-1] Push в bare remote

- **WHEN** у репозитория есть remote и создан лейн T
- **THEN** ветка `harness/T` существует в remote

#### Scenario: [CASE-PUSH-1-2] Нет gh

- **WHEN** `gh` отсутствует
- **THEN** `lane pr` отказывает `R-PUSH-1`, PR не создаётся

### Requirement: [REQ-RB-1] Откат частичного финализатора

Если после сервисных коммитов финализатор падает, ДОЛЖЕН (MUST) выполнить
`reset --soft` до предыдущего HEAD каждого сервиса; изменения остаются в индексе.

#### Scenario: [CASE-RB-1-1] Откат сервисного коммита

- **WHEN** сервис закоммичен, затем финализатор падает
- **THEN** HEAD сервиса возвращён, файл изменён, записи `commits` удалены

#### Scenario: [CASE-RB-1-2] Откат главного коммита

- **WHEN** главный коммит уже сделан, затем падает следующий шаг
- **THEN** главный HEAD откатывается `reset --soft`, изменения сохранены

### Requirement: [REQ-RB-2] Evidence не врёт

Записи о коммитах, откаченных финализатором, НЕ ДОЛЖНЫ (MUST NOT) оставаться в
`commits`/`tasks.commit_sha`.

#### Scenario: [CASE-RB-2-1] Удалённая запись

- **WHEN** откат выполнен
- **THEN** `store.get_commit(sha)` не находит коммит
