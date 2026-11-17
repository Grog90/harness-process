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
же коммит своим ключом. Checkout — `fetch-depth: 0`; PR-база фетчится без
`--depth`, иначе диапазон захватывает историю.

## Финализатор и merge

Если в основном репозитории `commit.gpgsign=true` (или выставлен
`HARNESS_REQUIRE_SIGNED=1`), `harness finalize` и `harness lane merge`:

1. до коммита проверяют `user.signingkey`; без ключа — отказ `R-FINAL-2`;
2. коммитят main, сервисы и merge с `-S`, используя `gpg.format`,
   `user.signingkey` и `allowedSignersFile` основного репозитория;
3. после коммита вызывают `git verify-commit`; неподписанный коммит откатывается.

Сервисные репозитории не нуждаются в собственном конфиге подписи: ключ и
allowed_signers берутся из основного. Это **tamper-evident**, а не
tamper-proof: финализатор живёт в том же дереве.

## GitHub

Публичный ключ загружен в GitHub как **Signing Key** (id `1340700`, title
«harness signing key», 17.11.2026). Коммиты после `c84537a` отображаются как
**Verified**; проверка: `gh api repos/Grog90/harness-process/commits/<sha>
--jq '.commit.verification.verified'`.

При ротации новый публичный ключ нужно загрузить в GitHub заново (старый можно
оставить до проверки старых коммитов).

## Ротация (два push)

Порядок важен: коммит, меняющий allowed_signers, должен быть подписан **старым**
ключом, иначе before-якорь его не примет.

1. `ssh-keygen -t ed25519 -f ~/.ssh/harness_signing_new`.
2. Закоммитить новый публичный ключ в `.github/allowed_signers`, **подписав
   старым** ключом; дождаться зелёного CI.
3. `git config --local user.signingkey <новый>`.
4. Следующие коммиты подписаны новым ключом. Старые коммиты после удаления
   старой строки становятся unverified — это ожидаемо.

## Ограничения

- merge/squash через GitHub UI создаёт неподписанный коммит на main; вливать
  нужно локальным подписанным merge, иначе CI покраснеет;
- ключ без пароля: безопасность машины = безопасность подписи;
- branch protection на private free недоступна; обязательность — CI/runbook;
- подпись не заменяет review и evidence.
