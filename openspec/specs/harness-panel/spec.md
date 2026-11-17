# harness-panel Specification

## Purpose

Показать состояние harness в композере DSH, не уходя в отдельный браузер.

## Requirements

### Requirement: [REQ-PANEL-1] Панель зарегистрирована в DSH

Бандл `@local/harness-panel` ДОЛЖЕН (SHALL) регистрировать client-панель в
`conversation.composer.dock` с собственным id `harness-panel` и не заменять
встроенные элементы.

#### Scenario: [CASE-PANEL-1-1] Регистрация

- **WHEN** бандл установлен в профиль
- **THEN** в `conversation.composer.dock` есть occupant `harness-panel`, порядок 20

### Requirement: [REQ-PANEL-2] Данные и offline

Панель ДОЛЖНА (SHALL) читать `/metrics.json` доски, обновляться раз в 15 с и при
недоступности показывать `доска offline` с командой запуска; сервер доски отдаёт
CORS-заголовок.

#### Scenario: [CASE-PANEL-2-1] Доска доступна

- **WHEN** `harness board --serve` запущен
- **THEN** панель показывает changes/tasks/rework

#### Scenario: [CASE-PANEL-2-2] Доска недоступна

- **WHEN** fetch не удался
- **THEN** виден offline и команда `board --serve`, панель не падает

### Requirement: [REQ-PANEL-3] Только чтение

Панель НЕ ДОЛЖНА (MUST NOT) вызывать accept/release/commit и менять состояние
процесса.

#### Scenario: [CASE-PANEL-3-1] Нет мутаций

- **WHEN** проверяется client.js
- **THEN** в нём только fetch metrics и локальный refresh
