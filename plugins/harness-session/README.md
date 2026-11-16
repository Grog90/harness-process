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

Плагин не установлен по умолчанию: установка меняет профиль DSH и делается
отдельным решением владельца. Без установки `harness-guard` продолжает работать
самостоятельно.

## Проверка

```powershell
node --test plugins/harness-session/session-core.test.mjs
python -m unittest tests.test_session_adapter -v
```
