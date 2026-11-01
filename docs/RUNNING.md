# Как запускать задачи через пайплайн

Короткий ответ: **одна команда**, где исполнитель и проверяющий — любые внешние
команды.

```powershell
python -m harness run --task FEE-101 --brief briefs/fee-101.json `
  --worker  "my-agent --brief %HARNESS_BRIEF% --root %HARNESS_ROOT%" `
  --verifier "python examples/verifiers/behavior_verifier.py" `
  --commit
```

Дальше — три способа (по возрастанию ручного контроля), контракты команд и что
делать, когда прогон остановился.

---

## Способ 1. Одна команда (обычный режим)

```powershell
python -m harness run --task FEE-101 --brief briefs/fee-101.json `
  --worker   "claude -p 'выполни бриф из %HARNESS_BRIEF%'" `
  --verifier "python examples/verifiers/behavior_verifier.py" `
  --commit
```

Что происходит по шагам:

```
создана задача            → CLAIMED
бриф принят               → BRIEF_READY     (валидация: поля, противоречия, критерии)
попытка 1: диспетч        → RUNNING         (бюджет попыток и стоимости проверен до старта)
воркер отработал          → exit 0
снимок принят             → VERIFYING       (sha256 по дереву работы)
вердикт: FAIL             → REWORK_READY    (пакет доработки записан для следующей попытки)
попытка 2: диспетч        → RUNNING
вердикт: PASS             → ACCEPTED
коммит <sha>              → COMMITTED       (write-set ⊆ allowed_files, журнал в том же коммите)
```

Флаги:

| Флаг | Что делает |
|---|---|
| `--worker CMD` | обязателен: команда исполнителя |
| `--verifier CMD` | команда проверяющего; без неё прогон остановится в `VERIFY_BLOCKED` |
| `--adversarial CMD` | второй проверяющий; его FAIL перекрывает PASS |
| `--commit` | закоммитить принятое финализатором (иначе остановка на `ACCEPTED`) |
| `--lane` | работать в отдельном `git worktree` |
| `--merge` | после коммита слить лейн в главное дерево (включает `--commit`) |
| `--timeout N` | лимит на один запуск воркера/проверяющего |
| `--scope DIR` | сузить область снимка (например, `src`) |

## Способ 2. Пошагово руками

Полезно, когда надо посмотреть на каждый шаг или вклиниться между ними.

```powershell
python -m harness init
python -m harness task new --id FEE-101 --spec spec-fee-3 --base-commit 9f3c1de
python -m harness brief check briefs/fee-101.json
python -m harness brief attach --task FEE-101 briefs/fee-101.json
python -m harness dispatch --task FEE-101 --worker w1

# ... агент работает (через шлюз) ...

python -m harness submit --task FEE-101 --attempt 1 --snapshot .
python -m harness checks run --task FEE-101 --verdict-out verdict.json   # проверки в копии дерева
python -m harness verdict --task FEE-101 verdict.json
python -m harness finalize --task FEE-101 --message "fee: 2.5%"
```

## Способ 3. Из своего кода

Если у вас уже есть цикл агента, оберните инструменты клиентом — и он же может
запускать шаги:

```python
from examples.harness_client import HarnessClient, ToolDenied

client = HarnessClient(root=".", role="worker", task_id="FEE-101")
try:
    client.write("src/fee.py", patch)      # через шлюз, с проверкой allowed_files
except ToolDenied as exc:
    print(exc.rule, exc.reason)            # R-WORK-1: ...
client.submit()                            # снимок + VERIFYING
```

Полный драйвер доступен и как Python API:

```python
from harness.run import RunConfig, Runner

config = RunConfig(
    root=".", task_id="FEE-101", brief_path="briefs/fee-101.json",
    worker_cmd="my-agent --brief $HARNESS_BRIEF",
    verifier_cmd="python examples/verifiers/behavior_verifier.py",
    commit=True, lane=True, merge=True,
)
result = Runner(config, store, policy, engine).run()
print(result.state, result.commit_sha)
```

---

## Контракт исполнителя (воркера)

Воркер — это любая команда. Харнесс не знает, LLM внутри или скрипт.

**Окружение:**

| Переменная | Значение |
|---|---|
| `HARNESS_ROOT` | где лежат файлы (главное дерево или worktree лейна) |
| `HARNESS_CONTROL_ROOT` | где политика и состояние (в лейне — главное дерево) |
| `HARNESS_STATE` | путь к хранилищу состояния |
| `HARNESS_ROLE` | `worker` |
| `HARNESS_TASK_ID`, `HARNESS_ATTEMPT` | задача и номер попытки |
| `HARNESS_BRIEF` | путь к JSON брифа |
| `HARNESS_ALLOWED_FILES` | JSON-список разрешённых путей |
| `HARNESS_FEEDBACK` | путь к пакету доработки (только на повторных попытках) |

**Правила:**

1. Работать только через шлюз (`python -m harness tool --stdin` или `HarnessClient`).
2. Писать только в `allowed_files`.
3. Вернуть `0`, если работа сделана. Любой другой код — инфраструктурный сбой:
   он фиксируется как `ERROR`, а не как дефект кода, и задача уходит в `VERIFY_BLOCKED`.

### Ресурсы лейна

Лейн изолирует не только файлы, но и среду исполнения: при `harness lane create`
задача получает слот, а из слота считается набор ресурсов.

| Переменная | Что это |
|---|---|
| `HARNESS_LANE_INDEX` | номер слота (целое, уникален среди живых лейнов) |
| `HARNESS_LANE_PORT_BASE` | база портов: `18000 + слот × 10` |
| `HARNESS_TEST_DB` | `.harness/lanes/<слот>/test.db` |
| `HARNESS_LANE_TMP` | `.harness/lanes/<слот>/tmp` |

Переменные видят команды проверки (`harness checks run`), а не произвольные процессы:
они добавляются к белому списку окружения и передаются в контейнер через `-e`.
Задача без лейна переменных не получает — поведение прежнее.

```powershell
python -m harness lane create --task FEE-101 --json   # выдаёт слот и набор ресурсов
python -m harness lane show --task FEE-101            # тот же набор, без создания
python -m harness lane release --task FEE-101         # освободить слот, worktree оставить
python -m harness lane remove --task FEE-101          # удалить worktree и слот
```

Слот живого лейна не выдаётся второй раз; слот лейна, чей worktree исчез, освобождается
при следующем создании. Если порт базы занят посторонним процессом, берётся следующий
слот; когда заняты все 50 — отказ `R-WAVE-1` с причиной.

## Контракт проверяющего

**Вход:** JSON на stdin и переменные окружения.

```json
{"task_id": "FEE-101", "attempt": 1, "candidate_dir": "...", "snapshot_id": "snap_…", "brief": {…}}
```

| Переменная | Значение |
|---|---|
| `HARNESS_ROLE` | `verifier` или `adversarial` |
| `HARNESS_CANDIDATE` | каталог проверяемого кандидата |
| `HARNESS_SNAPSHOT` | снимок, к которому обязан привязаться вердикт |
| `HARNESS_VERDICT_OUT` | путь, куда положить вердикт |
| `HARNESS_EVIDENCE` | каталог для артефактов проверки |

**Выход:** вердикт в `HARNESS_VERDICT_OUT` **или** JSON в stdout. Минимально:

```json
{
  "verdict_id": "v-1", "task_id": "FEE-101", "attempt": 1, "snapshot_id": "snap_…",
  "verdict": "PASS",
  "reviewer_id": "verifier-1", "reviewer_role": "verifier",
  "criteria": [{"id": "AC-1", "result": "pass", "evidence": "pytest -k fee: 3 passed"}],
  "findings": []
}
```

Если `snapshot_id` не совпадёт с проверенным — вердикт отклоняется (`R-SNAPSHOT-1`).
Если критерий не проверен — вердикт понижается до `INCONCLUSIVE`, а не становится PASS.

---

## Пример с нуля

```powershell
# 0. зависимости: только Python 3.10+
cd мой-проект

# 1. политика и состояние (копируются один раз из репозитория харнесса)
copy ..\harness\policies . -Recurse
python -m harness init

# 2. бриф: скопируйте templates/brief.example.json и заполните под задачу
python -m harness brief check briefs/fee-101.json

# 3. прогон
python -m harness run --task FEE-101 --brief briefs/fee-101.json `
  --worker "python my_agent.py" `
  --verifier "python examples/verifiers/behavior_verifier.py" `
  --commit

# 4. что получилось
python -m harness task status --task FEE-101
git log --oneline -3
```

---

## Когда прогон остановился

| Состояние | Что значит | Куда смотреть |
|---|---|---|
| `VERIFY_BLOCKED` | проверка не состоялась: нет вердикта, таймаут, неполное покрытие | `task status`, `evidence/`, `events` |
| `ESCALATED` | три попытки исчерпаны, решение за человеком | `task status`, `.harness/feedback/` |
| `TIMED_OUT` | сторож отозвал полномочия по сроку | `harness watchdog sweep`, `events` |
| `REWORK_READY` после `--commit` | финализатор отказал: правка после PASS или посторонний файл | сообщение с правилом `R-SNAPSHOT-1` / `R-WRITESET-1` |

Разбор конкретного случая:

```powershell
python -m harness task status --task FEE-101 --json     # состояние, попытки, вердикты
python -m harness --json task status --task FEE-101 | ConvertFrom-Json | Select-Object -ExpandProperty events
```

---

## Внешние сервисы и MCP

Прогон `harness` от них не зависит: это инструменты агента, а не части цикла.
Если сервис лежит, цикл работает как обычно, а инструмент отвечает ошибкой.

| Что | Где | Как проверить |
|---|---|---|
| Нода xerj (поиск по коду/докам) | `127.0.0.1:9200`, задача планировщика `xerj-node` | `curl http://127.0.0.1:9200/` |
| Браузер moli | `.tools/moli/pkg/*/moli.exe` | `moli --version` |
| MCP-серверы | `~/.dsh/profiles/<profile>/cordis.patch.yml`, блок `- insert:` | `Get-CimInstance Win32_Process \| Where-Object CommandLine -like '*_mcp.py*'` |

Три сервера: `browser` (страницы, скриншоты), `xerj` (поиск), `trendshift` (тренды).
Индекс обновляется вручную — `xerj autoindex . --prefix harness`; без ноды `xerj_*`
отвечают ошибкой, а не пустым списком. Почему каталоги данных помечены Low integrity и
что это значит — в [`XERJ.md`](XERJ.md).

---

## Рядом с прогоном

```powershell
# сторож: отзывает зависшие запуски, лучше отдельным процессом или по cron
python -m harness watchdog serve --interval 30

# проверки отдельно от прогона (например, в CI)
python -m harness checks run --task FEE-101 --verdict-out verdict.json

# стоимость
python -m harness cost record --task FEE-101 --amount 3.5
python -m harness cost report
```

## Ограничения, о которых стоит знать заранее

1. **Один воркер на задачу за раз.** Параллельность — через `--lane` (разные задачи),
   внутри одной задачи попытки последовательны.
2. **Бюджет стоимости не считается сам.** Нужно передавать факт (`cost record` или
   поле `cost` в вердикте); гейт работает по `control.budget` в брифе.
3. **Проверяющий запускается в каталоге кандидата** с урезанным окружением, но без
   ОС-изоляции: сеть доступна. Для изоляции используйте `harness checks run --sandbox docker`.
4. **`--commit` — настоящий git-коммит.** Если репозиторий грязный до задачи,
   финализатор остановится: посторонние файлы нужно закоммитить или убрать заранее.
