# Spec Delta

## Purpose

Change может объявлять несколько сервисов, verification прогоняет каждый, а
release/acceptance/evidence собирают версии всех.

## ADDED Requirements

### Requirement: [REQ-MULTI-1] Change объявляет сервисы и их версии

Система ДОЛЖНА (SHALL) принимать `change new --service name=PATH[@SHA]`, хранить
сервисы в change и отдавать их в брифе с относительным путём и полным SHA.

#### Scenario: [CASE-MULTI-1-1] Сервис попадает в бриф

- **WHEN** change создан с `--service api=../api@<40hex>`
- **THEN** `brief from-change` содержит services с name=api и base_commit

#### Scenario: [CASE-MULTI-1-2] Пустой путь отклоняется валидацией брифа

- **WHEN** services[].path пустой
- **THEN** brief.validate выдаёт E_BAD_SERVICES

### Requirement: [REQ-MULTI-2] Verification прогоняет каждый сервис

Система ДОЛЖНА (SHALL) писать прогоны по ключу `service:mode` и требовать baseline и
candidate для каждого сервиса, объявленного в change; нулевой прогон и новые падения
блокируют проверку.

#### Scenario: [CASE-MULTI-2-1] Второй сервис без candidate

- **WHEN** для сервиса api записан только baseline
- **THEN** check_report сообщает «нет прогона candidate» и не пропускает VERIFIED

#### Scenario: [CASE-MULTI-2-2] Прогон сервиса не объявлен

- **WHEN** в change объявлен api, а verification содержит только repo
- **THEN** check_report сообщает «нет прогонов сервиса api»

### Requirement: [REQ-MULTI-3] Приёмка собирает версии всех сервисов

Release/acceptance ДОЛЖНЫ (MUST) принимать несколько `--service name=SHA`, а evidence
включать записи с этими версиями.

#### Scenario: [CASE-MULTI-3-1] Несколько SHA в acceptance

- **WHEN** acceptance записан с api=<sha1> и web=<sha2>
- **THEN** acceptance.json содержит оба сервиса, archive проверяет их

### Requirement: [REQ-MULTI-4] Порядок сервисов — это merge order

Порядок, в котором сервисы объявлены в change, ДОЛЖЕН (MUST) сохраняться в
release/acceptance (merge_order); запись с другим порядком отклоняется, evidence
фиксирует порядок.

#### Scenario: [CASE-MULTI-4-1] Обратный порядок отклоняется

- **WHEN** change объявляет api,web, а release записан web,api
- **THEN** release отклоняется с R-ACCEPT-1 и упоминанием merge order

#### Scenario: [CASE-MULTI-4-2] Порядок попадает в записи

- **WHEN** release/acceptance записаны в порядке плана
- **THEN** в обоих JSON есть merge_order = [api, web]
