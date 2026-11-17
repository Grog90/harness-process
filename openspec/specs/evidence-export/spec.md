# evidence-export Specification

## Purpose

Дать evidence вторую копию вне GitHub artifacts и машинную проверку целостности.

## Requirements

### Requirement: [REQ-EVID-1] Экспорт с манифестом

`harness evidence export --dest D [--change C]` ДОЛЖЕН (SHALL) скопировать
evidence и записать sha256-манифест; отсутствие источника или совпадение
source/dest — отказ `R-EVID-1`.

#### Scenario: [CASE-EVID-1-1] Успешный экспорт

- **WHEN** evidence/change-x существует
- **THEN** файлы скопированы, манифест содержит sha256, verify проходит

#### Scenario: [CASE-EVID-1-2] Нет источника

- **WHEN** каталога evidence/change-missing нет
- **THEN** отказ `R-EVID-1`, ничего не создано

### Requirement: [REQ-EVID-2] Проверка целостности

`harness evidence verify --dest D` ДОЛЖЕН (SHALL) обнаруживать пропавший файл или
несовпадение sha256 и возвращать `ok=false`.

#### Scenario: [CASE-EVID-2-1] Порча копии

- **WHEN** файл в назначении изменён
- **THEN** verify сообщает «хэш не совпал»

#### Scenario: [CASE-EVID-2-2] Нет манифеста

- **WHEN** манифеста нет
- **THEN** отказ `R-EVID-1`, а не пустой ok
