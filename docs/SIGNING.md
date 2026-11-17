# Подпись коммитов

## Ключ

- приватный: `~/.ssh/harness_signing` (ed25519, без пароля, вне репозитория);
- публичный: `.github/allowed_signers` (в репозитории — это и есть доверие).

Проверка:

```powershell
git verify-commit HEAD
git log --show-signature -1
```

## Настройка (уже сделана локально)

```powershell
git config --local gpg.format ssh
git config --local user.signingkey "$env:USERPROFILE\.ssh\harness_signing"
git config --local commit.gpgsign true
git config --local gpg.ssh.allowedSignersFile .github/allowed_signers
```

## CI

Workflow настраивает `gpg.ssh.allowedSignersFile` и выполняет
`git verify-commit HEAD`; неподписанный коммит валит job.

## GitHub

Пока публичный ключ не загружен в GitHub (`Settings → SSH and GPG keys → New SSH
key`, тип **Signing Key**), GitHub UI показывает «Unverified». Это не отменяет
проверку в CI; загрузка — отдельное действие владельца.

## Ротация

1. `ssh-keygen -t ed25519 -f ~/.ssh/harness_signing_new`.
2. Заменить строку в `.github/allowed_signers`.
3. `git config --local user.signingkey <новый>`.
4. Новые коммиты подписаны новым ключом; старые проверяются старым, пока он в
   allowed_signers (если удалить — старые станут unverified).

## Ограничения

- ключ без пароля: безопасность машины = безопасность подписи;
- branch protection на private free недоступна; обязательность — CI/runbook;
- подпись не заменяет review и evidence.
