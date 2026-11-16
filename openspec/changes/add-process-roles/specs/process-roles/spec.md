# Spec Delta

## Purpose

Роли процесса становятся исполняемыми: BA/SA не пишут код, DEV не правит спеку,
QA пишет тесты, привилегированные инструменты закрыты, а открытый вопрос требует
ответа человека, а не догадки.

## ADDED Requirements

### Requirement: [REQ-ROLE-1] Роли analyst и qa имеют разные области записи

Политика ДОЛЖНА (SHALL) различать `analyst` (BA/SA), `qa` и `worker`: analyst пишет
только спеку и документы, qa — тесты и кейсы, worker — код в `allowed_files`.

#### Scenario: [CASE-ROLE-1-1] Worker не правит спеку

- **WHEN** worker пишет `openspec/changes/.../spec.md`, даже если путь есть в allowed_files
- **THEN** вызов отклоняется с R-WORK-3

#### Scenario: [CASE-ROLE-1-2] QA пишет тесты, но не код сервиса

- **WHEN** qa пишет `tests/test_x.py` из allowed_files
- **THEN** запись разрешена; запись в `src/x.py` отклоняется

### Requirement: [REQ-ROLE-2] Процессная роль из брифа определяет права задачи

Диспетч ДОЛЖЕН (SHALL) брать `actor_role` брифа (BA/SA/DEV/QA/verifier) и публиковать
соответствующую роль политики в области прав; роли не смешиваются в одной задаче.

#### Scenario: [CASE-ROLE-2-1] Бриф BA получает analyst

- **WHEN** бриф с `actor_role: BA` диспетчится
- **THEN** `guard-scope.json` содержит роль `analyst`, и запись в код отклоняется

#### Scenario: [CASE-ROLE-2-2] Без actor_role поведение прежнее

- **WHEN** в брифе нет `actor_role`
- **THEN** роль — `worker`, как до Этапа 2

### Requirement: [REQ-TOOL-1] Привилегированные инструменты закрыты рабочим ролям

Рабочие роли ДОЛЖНЫ (MUST) получать отказ R-TOOL-2 на `plugin_manager`, marketplace
и опасные MCP-префиксы; человек и system сохраняют доступ.

#### Scenario: [CASE-TOOL-1-1] Worker не ставит плагины

- **WHEN** worker вызывает `plugin_manager`
- **THEN** вызов отклоняется с R-TOOL-2

#### Scenario: [CASE-TOOL-1-2] Читающий MCP разрешён

- **WHEN** worker вызывает `mcp__xerj__xerj_search`
- **THEN** вызов разрешён

### Requirement: [REQ-GAP-1] Открытый GAP требует ответа человека

При открытом `.harness/gap.json` пишущий вызов ДОЛЖЕН (SHALL) превращаться в `ask`
человеку; чтение не блокируется; снятие GAP возвращает обычный режим.

#### Scenario: [CASE-GAP-1-1] Запись при открытом вопросе

- **WHEN** опубликован GAP и агент вызывает write_file
- **THEN** решение — ask с вопросом, а не allow

#### Scenario: [CASE-GAP-1-2] Чтение не блокируется

- **WHEN** опубликован GAP и агент читает файл
- **THEN** вызов разрешён

### Requirement: [REQ-PRESET-1] Процессные пресеты и скиллы установлены

DSH ДОЛЖЕН (SHALL) показывать пять активных пресетов process-* и скиллы process-* в
каталоге рабочего каталога; пресет не выдаёт роли инструментов сверх политики.

#### Scenario: [CASE-PRESET-1-1] Пресеты активны

- **WHEN** профиль загружен после установки бандла
- **THEN** строки preset-process-ba/sa/dev/qa/verifier активны
