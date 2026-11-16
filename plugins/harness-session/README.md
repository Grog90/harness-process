# harness-session

Host-плагин DSH: активная задача harness видна сессии, а её статус можно прочитать
инструментом.

## Что делает

- если найден `.harness/guard-scope.json`, добавляет в системный контекст
  `task_id`, роль, фазу, попытку, `allowed_files` и сервисы;
- регистрирует read-only `harness_task`, который зовёт
  `python -m harness --json task status --task <id>`;
- ничего не диспетчит, не выносит вердикт и не коммитит.

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
