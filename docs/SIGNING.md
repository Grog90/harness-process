# Подпись коммитов

## Ключ

- приватный: `~/.ssh/harness_signing` (ed25519, без пароля, вне репозитория);
- публичный: `.github/allowed_signers` (в репозитории — это и есть доверие).
  `git verify-commit` сверяет ключ, а не committer-email, поэтому синтетическая
  личность финализатора `harness@harness.local` отдельной строки не требует.

Проверка:

```powershell
git verify-commit HEAD
git log --show-signature -1
```

## Настройка (как настроить)

```powershell
git config --local gpg.format ssh
git config --local user.signingkey "$env:USERPROFILE\.ssh\harness_signing"
git config --local commit.gpgsign true
git config --local gpg.ssh.allowedSignersFile .github/allowed_signers
```

## CI

Workflow настраивает `gpg.ssh.allowedSignersFile` и выполняет
`git verify-commit` для каждого коммита диапазона (`before..HEAD` на push,
`base..head` на PR); неподписанный коммит валит job. Доверенный ключ берётся из
`before`/base, а не из проверяемого дерева — иначе автор мог бы подписать свой
же коммит своим ключом. Checkout — `fetch-depth: 0`.

## GitHub

Пока публичный ключ не загружен в GitHub (`Settings → SSH and GPG keys → New SSH
key`, тип **Signing Key**), GitHub UI показывает «Unverified». Это не отменяет
проверку в CI; загрузка — отдельное действие владельца.

## Ротация

1. `ssh-keygen -t ed25519 -f ~/.ssh/harness_signing_new`.
2. Заменить строку в `.github/allowed_signers` на новый публичный ключ.
3. `git config --local user.signingkey <новый>`.
4. Новые коммиты подписаны новым ключом; старые проверяются старым, пока он в
   allowed_signers (если удалить — старые станут unverified).

## Ограничения

- merge/squash через GitHub UI создаёт неподписанный коммит на main; вливать
  нужно локальным подписанным merge, иначе CI покраснеет;
- ключ без пароля: безопасность машины = безопасность подписи;
- branch protection на private free недоступна; обязательность — CI/runbook;
- подпись не заменяет review и evidence.
