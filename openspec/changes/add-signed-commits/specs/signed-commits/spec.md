# Spec Delta

## Purpose

Сделать авторство коммитов проверяемым, а не заявленным.

## ADDED Requirements

### Requirement: [REQ-SIGN-1] Коммиты подписаны

Все коммиты harness ДОЛЖНЫ (SHALL) создаваться с SSH-подписью; публичный ключ
лежит в `.github/allowed_signers`, приватный — вне репозитория.

#### Scenario: [CASE-SIGN-1-1] HEAD проверяется

- **WHEN** выполняется `git verify-commit HEAD`
- **THEN** подпись валидна по `.github/allowed_signers`

#### Scenario: [CASE-SIGN-1-2] Ключ не в репозитории

- **WHEN** проверяется дерево
- **THEN** приватного ключа в нём нет

### Requirement: [REQ-SIGN-2] CI проверяет подпись

CI ДОЛЖЕН (SHALL) настраивать `gpg.ssh.allowedSignersFile` и падать, если HEAD
не подписан или подпись не совпала.

#### Scenario: [CASE-SIGN-2-1] CI-шаг

- **WHEN** workflow выполняется на push
- **THEN** есть шаг `git verify-commit HEAD`

### Requirement: [REQ-SIGN-3] Документация

`docs/SIGNING.md` ДОЛЖЕН (SHALL) описывать ключ, проверку, ограничение GitHub и
ротацию.

#### Scenario: [CASE-SIGN-3-1] Документ

- **WHEN** читается docs/SIGNING.md
- **THEN** есть ключ/проверка/GitHub/ротация
