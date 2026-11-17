# Runbook развёртывания

Дата: 2026-11-17. Репозиторий: <https://github.com/Grog90/harness-process> (private).

## Что уже развёрнуто

| Слой | Состояние |
|---|---|
| GitHub Actions `ci` | зелёный на `main`; unittest + `harness ci` + `rule verify` |
| cosign keyless | evidence подписывается и проверяется в том же прогоне |
| Artifact `harness-ci-evidence` | `report.json` + `report.json.bundle`, retention GitHub |
| DSH-плагин `harness-session` | установлен в профиль `desktop`, `harness_task` жив |
| Привязка сессии | `harness session attach --task` + автодиспетч на `agent/created` |
| Branch protection | **недоступен** на free-private; зелёный CI — по runbook |

## Запуск изменения

```powershell
python -m harness task new --id T-1 --change <change> --spec "openspec:<change>@<digest>"
python -m harness brief from-change --change <change> --task T-1 --out brief.json
python -m harness brief attach --task T-1 brief.json
python -m harness session attach --task T-1
# открыть DSH-сессию: плагин вызовет dispatch и опубликует guard-scope
python -m harness task status --task T-1
```

Проверка привязки: `.harness/session-attach.json` существует, после старта сессии
появился `.harness/guard-scope.json` с `task_id`. Ошибка автодиспетча —
`.harness/session-attach.error`.

Отвязка:

```powershell
python -m harness session detach
```

## CI и evidence

```powershell
gh run list --repo Grog90/harness-process
gh run watch <id> --repo Grog90/harness-process --exit-status
gh run download <id> -n harness-ci-evidence -D evidence/ci-download
cosign verify-blob --bundle evidence/ci-download/report.json.bundle \
  --certificate-identity-regexp "https://github.com/Grog90/harness-process/.*" \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  evidence/ci-download/report.json
```

Подпись keyless: долгоживущего ключа нет, identity привязана к workflow и OIDC.
Хранилище — GitHub artifact; для долгого хранения нужен внешний bucket (не сделано).

## Ограничения

- **Branch protection** требует GitHub Pro или public. Решение: оставить private
  и не публиковать код ради защиты; зелёный `ci` обязателен по runbook, а не
  технически. Branch protection — защита git-репозитория (прямой push/force-push/
  merge красного PR), а не замена harness-гейтам. Вернуться к вопросу при появлении
  команды или GitHub Pro.
- **DEEPSEEK_API_KEY** в CI не задан: headless smoke не входит в workflow.
  Детерминированные тесты ключ не требуют.
- **Windows checks** работают в copy-only: docker Linux bind-mount на Windows
  отключён намеренно (`harness/checks.py`).
- **Один attach на корень**: две одновременные задачи в одном профиле не
  поддерживаются.
- **Auto-submit** — opt-in (`--auto-submit`), только для коротких задач: первый
  turn может быть неполным. **Auto-verdict/accept** не автоматизированы; commit —
  отдельный гейт.
- **Публикация лейна:** `harness lane push --task T` (без force) и
  `harness lane pr --task T --target main` (через `gh`).
- **Статус PR:** `harness forge status --task T [--wait --timeout 300]` — читает
  checks через `gh`, без `gh` отказывает.
- **Вывоз evidence:** `harness evidence export --dest <каталог> [--change C]`,
  проверка — `harness evidence verify --dest <каталог>`.
- **Панель в DSH:** бандл `plugins/harness-panel` ставится в профиль `desktop`;
  данные читает с `harness board --serve --port 8765`, offline показывает команду
  запуска. Адрес можно задать в `localStorage.harnessBoardUrl`.
- **Multi-service e2e:** `harness e2e run --change C --command "..." --service api=services/api --service web=services/web`;
  копии сервисов, env `HARNESS_E2E_<NAME>_DIR/_SHA`, отчёт `evidence/e2e-C.json`.
- **5 старых change** закрываются как исторические (CANCELLED), не через полный
  процесс.

## Восстановление

1. CI красный: `gh run view <id> --log-failed`; локально повторить
   `python -m harness ci --run-tests`.
2. Плагин не диспетчит: проверить `.harness/session-attach.error`, затем вручную
   `python -m harness dispatch --task <id> --primary-agent <agent>`.
3. cosign упал: повторить workflow; keyless не требует ключа. Если OIDC недоступен,
   CI-шаг падает, отчёт всё равно в artifact (upload `if: always()`).
4. Оторвалась привязка: `session detach`, затем `session attach --task`.

## Ротация и доступ

- GitHub OIDC/токен: `gh auth status`; токен не хранится в репозитории.
- Плагин: локальная установка из `plugins/harness-session`; обновление —
  `plugin_manager install_bundle` заново.
- Секреты: `DEEPSEEK_API_KEY` — только GitHub Secret, если включаем headless.
