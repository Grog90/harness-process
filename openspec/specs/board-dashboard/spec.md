# board-dashboard Specification

## Purpose

Сделать доску читаемой и доступной внутри DSH, не меняя данные процесса.

## Requirements

### Requirement: [REQ-BOARD-1] Полная доска в DSH

Плагин ДОЛЖЕН (SHALL) регистрировать `main` с ключом `harness-board` и открывать
его из компактной панели через `layout.selectPanel`.

#### Scenario: [CASE-BOARD-1-1] Открытие

- **WHEN** пользователь нажимает «открыть доску»
- **THEN** выбран main-панель `harness-board`, видны KPI/фильтры/список

### Requirement: [REQ-BOARD-2] Фильтры и приоритет

Полная доска ДОЛЖНА (SHALL) по умолчанию скрывать `ACCEPTED/ARCHIVED/CANCELLED`,
поддерживать фильтры `Активные/READY/Блок/Архив/Все` и поиск по change.

#### Scenario: [CASE-BOARD-2-1] Активные

- **WHEN** открыта доска
- **THEN** видны только незавершённые change

### Requirement: [REQ-BOARD-3] HTML-доска

HTML-доска ДОЛЖНА (SHALL) иметь тёмную тему по умолчанию, sticky-заголовок,
объяснение пустой очереди, фильтры и поиск; `CANCELLED` скрыт по умолчанию.

#### Scenario: [CASE-BOARD-3-1] Фильтр Все

- **WHEN** нажата кнопка «Все»
- **THEN** показываются все change, включая отменённые

### Requirement: [REQ-BOARD-4] Проверки

Применение клиента и фильтры ДОЛЖНЫ (SHALL) быть покрыты исполняемыми тестами.

#### Scenario: [CASE-BOARD-4-1] Node-smoke

- **WHEN** запускается `node tests/panel_client_apply.test.mjs`
- **THEN** регистрируются `conversation.composer.dock` и `main/harness-board`
