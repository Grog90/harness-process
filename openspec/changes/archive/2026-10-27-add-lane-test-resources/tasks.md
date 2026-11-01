# Tasks

## 1. Слот и ресурсы в состоянии

- [x] 1.1 Добавить в `harness/state.py` таблицу `lane_resources(slot INTEGER PRIMARY KEY, task_id TEXT UNIQUE NOT NULL, created_at REAL NOT NULL)` через `executescript` рядом с остальными `CREATE TABLE IF NOT EXISTS`; проверить, что старая база открывается без ошибок (`python -m harness task list --json`)
- [x] 1.2 Реализовать в `harness/lanes.py` `resources_for(root, slot)` (база портов `18000 + slot*10`, `.harness/lanes/<slot>/test.db`, `.harness/lanes/<slot>/tmp`, индекс) и `reserve_slot(store, root, task_id)` с выбором первого свободного слота, пропуском занятых баз портов (`bind` на `127.0.0.1`) и отказом `R-WAVE-1` при исчерпании диапазона; проверить `python -m unittest tests.test_lanes_resources -v`
- [x] 1.3 Реализовать `release(store, task_id)` и ленивую очистку: слот с отсутствующим worktree считается свободным при следующем `reserve_slot`; проверить тестом «создать → удалить каталог worktree → создать снова»

## 2. Команды лейнов

- [x] 2.1 Расширить `cmd_lane` в `harness/cli.py`: `create` возвращает набор ресурсов, добавлены действия `show` и `release`; проверить `python -m harness lane create --task FEE-101 --json` и `python -m harness lane show --task FEE-101 --json`
- [x] 2.2 Освобождать слот в `lanes.remove` и в `release`, не освобождая его при `merge`; проверить тестом «после remove слот переиспользуется»
- [x] 2.3 Покрыть `show` для несуществующего лейна понятным отказом с правилом, а не трейсбеком; проверить `python -m harness lane show --task NOPE --json` возвращает `ok: false`

## 3. Передача ресурсов в проверки

- [x] 3.1 Добавить в `harness/checks.py` `lane_env()` и параметр `lane_resources` у `run()` и `_env()`: переменные добавляются поверх `SAFE_ENV`, остальной белый список не расширяется
- [x] 3.2 В docker-ветке `_execute` передать каждую переменную через `-e KEY=VALUE`; проверить тестом список аргументов `docker run`
- [x] 3.3 Передавать ресурсы из задачи в `checks.run` в `cmd_checks_run` через `_lane_resources`, не ломая вызовы без лейна

## 4. Правило и документация

- [x] 4.1 Сузить `residual_risk` у `R-WAVE-1` через `python -m harness rule change --id R-WAVE-1 --reason "..."`, затем `python -m harness rule verify` — состояние `current`, дрейфа нет
- [x] 4.2 Перегенерировать `docs/RULES_MATRIX.md` командой `python -m harness coverage --markdown`; текст остатка совпадает с `rules.json`
- [x] 4.3 Описать в `docs/RUNNING.md` раздел «Ресурсы лейна»: переменные, освобождение слота, исчерпание диапазона

## 5. Интеграция

- [x] 5.1 Расширить `c_lane_merge` в `harness/selftest.py` парой «разрешённый / запрещённый случай» (тесты — `tests/test_lanes_resources.py`): формула слота и показ совпадают, задача без лейна переменных не получает, слот освобождается вместе с лейном
- [x] 5.2 Прогнать полный набор: 230 unittest, `selftest`, `demo`, `coverage` — зелёные

## Workflow follow-up

- Решение по `docs/RULES_MATRIX.md`: файл остаётся вне `allowed_files` воркера — это генерируемый артефакт ревью, его перегенерирует финализатор, а не исполнитель.
- Архивировать change после ревью артефактов (`openspec archive add-lane-test-resources`).
- Проверить, что архив влил требования в `openspec/specs/lane-isolation/spec.md`.
