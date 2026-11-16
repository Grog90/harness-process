# Spec Delta

## Purpose

Проверка, выпуск, приёмка и закрытие изменения подтверждаются объектами с версиями:
двумя прогонами, записями release/acceptance/closing и пакетом доказательств.

## ADDED Requirements

### Requirement: [REQ-QA-1] Проверка требует baseline, candidate и версий

Система ДОЛЖНА (SHALL) требовать для перехода в VERIFIED отчёт с spec_ref, runner,
окружением, полными версиями сервисов и обоими прогонами; candidate ДОЛЖЕН (MUST)
пройти, новых падений относительно baseline быть не должно.

#### Scenario: [CASE-QA-1-1] Отчёт без версий блокирует проверку

- **WHEN** verification.json не содержит runner или сервисов
- **THEN** переход verify отклоняется с R-QA-1

#### Scenario: [CASE-QA-1-2] Новое падение блокирует проверку

- **WHEN** candidate падает на тесте, который в baseline проходил
- **THEN** verification_check возвращает ошибку, VERIFIED не наступает

### Requirement: [REQ-QA-2] Нулевой прогон не считается проверкой

Система ДОЛЖНА (SHALL) отклонять отчёт, где в baseline или candidate выполнено
0 тестов.

#### Scenario: [CASE-QA-2-1] Ноль тестов

- **WHEN** прогон сообщает 0 выполненных тестов
- **THEN** проверка отклоняется с R-QA-2

### Requirement: [REQ-ACCEPT-1] Выпуск, приёмка и архив требуют записей

Система ДОЛЖНА (SHALL) требовать release.json для выпуска (кроме L0), acceptance.json
с критериями для приёмки, closing.json с sync-флагами и evidence-манифест для архива.

#### Scenario: [CASE-ACCEPT-1-1] Приёмка без записи

- **WHEN** запрошен переход accept, а acceptance.json отсутствует
- **THEN** переход отклоняется с R-ACCEPT-1

#### Scenario: [CASE-ACCEPT-1-2] Архив без evidence

- **WHEN** closing.json заполнен, но пакета evidence нет
- **THEN** архив отклоняется с R-ACCEPT-1

### Requirement: [REQ-ACCEPT-2] L2 принимает человек

Для L2 выпуск и приёмка ДОЛЖНЫ (MUST) иметь `by_role`/`accepted_by_role` = human;
агент или CI отклоняются.

#### Scenario: [CASE-ACCEPT-2-1] Агент не выпускает L2

- **WHEN** release.json для L2 записан с by_role=agent
- **THEN** release_check отклоняет выпуск с R-ACCEPT-2
