# harness-session

Host-плагин DSH: активная задача harness видна сессии, а её статус можно прочитать
инструментом.

## Что делает

- если найден `.harness/guard-scope.json`, добавляет в системный контекст
  `task_id`, роль, фазу, попытку, `allowed_files` и сервисы;
- при `agent/created` и наличии `.harness/session-attach.json` один раз вызывает
  `harness dispatch --task <id> --primary-agent <agent>` и удаляет привязку;
- чужой активный scope не перезаписывает: пишет конфликт в error-файл;
- ошибка dispatch пишется в `.harness/session-attach.error`, успех удаляет старую;
- регистрирует read-only `harness_task`, который зовёт
  `python -m harness --json task status --task <id>`;
- не выносит вердикт и не коммитит.

## Привязка сессии

```powershell
python -m harness session attach --task T-1
# открыть DSH-сессию: dispatch произойдёт автоматически
python -m harness session status
python -m harness session detach
```

Один attach на корень: две одновременные задачи в одном профиле не поддерживаются.

## Корень и Python

Корень: `config.root` → `HARNESS_ROOT` → `~/.dsh/harness-root` → текущий каталог
вверх до `policies/policy.json`.
Python: `config.python` → `HARNESS_PYTHON` → `python`.

## Установка

```powershell
python -m harness plugin prepare
# затем в DSH: plugin_manager install_bundle target=<путь к plugins/harness-session>
```

В текущем профиле `desktop` плагин установлен 16.11.2026 (`application: applied`,
`harness_task` виден в списке инструментов). В поставку harness он не входит: на
другом профиле установка — отдельное решение владельца. Без установки
`harness-guard` продолжает работать самостоятельно.

## Проверка

```powershell
node --test plugins/harness-session/session-core.test.mjs
python -m unittest tests.test_session_adapter -v
```
