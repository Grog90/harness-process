# signed-commits Specification

## Purpose

Сделать авторство коммитов проверяемым, а не заявленным.

## Requirements

### Requirement: [REQ-SIGN-1] Коммиты подписаны

Все **новые** коммиты harness (начиная с `c84537a`) ДОЛЖНЫ (SHALL) создаваться
с SSH-подписью; публичный ключ лежит в `.github/allowed_signers`, приватный — вне
репозитория. Старые коммиты ретроспективно не подписываются.

#### Scenario: [CASE-SIGN-1-1] HEAD проверяется

- **WHEN** выполняется `git verify-commit HEAD`
- **THEN** подпись валидна по `.github/allowed_signers`

#### Scenario: [CASE-SIGN-1-2] Ключ не в репозитории

- **WHEN** проверяется дерево
- **THEN** приватного ключа в нём нет

### Requirement: [REQ-SIGN-2] CI проверяет подпись

CI ДОЛЖЕН (SHALL) настраивать `gpg.ssh.allowedSignersFile` и падать, если
проверяемый диапазон содержит неподписанный коммит; ошибка `rev-list` — тоже
отказ. Для PR доверенный якорь берётся из base-ветки, а не из проверяемого дерева.

#### Scenario: [CASE-SIGN-2-1] CI-шаг

- **WHEN** workflow выполняется на push/PR
- **THEN** каждый коммит диапазона проходит `git verify-commit`; при ошибке job падает

### Requirement: [REQ-SIGN-3] Документация

`docs/SIGNING.md` ДОЛЖЕН (SHALL) описывать ключ, проверку, ограничение GitHub и
ротацию.

#### Scenario: [CASE-SIGN-3-1] Документ

- **WHEN** читается docs/SIGNING.md
- **THEN** есть ключ/проверка/GitHub/ротация
