# Spec Delta

## MODIFIED Requirements

### Requirement: [REQ-ADAPT-3] Плагин не расширяет гейты

Плагин МОЖЕТ (MAY) выполнить один `dispatch` по явной привязке
`session-attach.json`, но НЕ ДОЛЖЕН (MUST NOT) выносить вердикт, коммитить,
принимать изменение или писать файлы продукта. Он читает scope и вызывает
read-only `task status` (тот может создать/мигрировать `.harness/state.db`).

#### Scenario: [CASE-ADAPT-3-1] Нет автоматических гейтов

- **WHEN** проверяется регистрация плагина
- **THEN** есть один dispatch по attach и read-only `harness_task`;
  verdict/commit/accept не регистрируются
