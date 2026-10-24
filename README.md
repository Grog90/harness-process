# harness — управляемый агентный цикл

Реализация практик из учебника «Агентные пайплайны» (Антон / Астра, издание 1):
роли с ограниченными правами, обязательный бриф, независимая проверка с вердиктом-объектом,
бюджет попыток, автомат состояний, матрица `RULE → HOOK → ENFORCEMENT` и измерение
самого контроля.

Зависимостей нет — только Python 3.10+ и стандартная библиотека.

## Быстрый старт

```powershell
python -m harness selftest        # 39 исполняемых проверок правил
python -m harness demo            # сквозной цикл, 43 проверки: шлюз, проверки, коммит, лейн
python -m harness coverage        # матрица правил и пробелы покрытия
python -m unittest discover -s tests -v
```

Ручной проход одной задачи:

```powershell
python -m harness init
python -m harness task new --id FEE-101 --spec spec-fee-3 --base-commit 9f3c1de
python -m harness brief check templates/brief.example.json
python -m harness brief attach --task FEE-101 templates/brief.example.json
python -m harness dispatch --task FEE-101 --worker worker-1
python -m harness submit --task FEE-101 --attempt 1 --snapshot src

# вердикт пишет проверяющий — вместе с тем снимком и той попыткой, которые он проверял
$st = python -m harness task status --task FEE-101 --json | ConvertFrom-Json
"{`"candidate_dir`":`"src`",`"snapshot_id`":`"$($st.task.verifying_snapshot)`",`"attempt`":$($st.task.attempt)}" |
  python examples/verifiers/behavior_verifier.py > verdict.json
python -m harness verdict --task FEE-101 verdict.json

python -m harness commit --task FEE-101 --snapshot src
python -m harness task status --task FEE-101
```

`templates/verdict.example.json` — образец формы, а не готовый вердикт: его
`snapshot_id` — заглушка, и такой файл будет отклонён по `R-SNAPSHOT-1`. Это
намеренно: вердикт, не привязанный к конкретному состоянию файлов, не принимается.

Любой отказ печатает правило и причину и возвращает код выхода `2`:

```
ОТКАЗ [R-SNAPSHOT-1] кандидат изменился после PASS: коммит запрещён, требуется повторная проверка
```

## Что уже держится механически

| Свойство | Как обеспечено | Проверка |
|---|---|---|
| Оркестратор не пишет код, воркер не выходит за `allowed_files` | политика ролей + хуки | `selftest` R-ORCH-1, R-WORK-1 |
| Нет брифа — нет диспетча; открытый вопрос блокирует запуск | guard `dispatch` | R-BRIEF-1, R-GAP-1 |
| Не больше 3 попыток, счётчик не сбрасывается сменой агента | SQLite, атомарное резервирование | R-BUDGET-1 |
| Нельзя коммитить без актуального PASS, привязанного к снимку | `snapshot_id` + guard `commit` | R-VERIFY-1, R-SNAPSHOT-1 |
| Правка после PASS отменяет вердикт | хеш дерева | R-SNAPSHOT-1 |
| «Не смог проверить» ≠ «проверил и плохо» | `INCONCLUSIVE`/`ERROR`/`TIMEOUT` | R-COMMIT-1 |
| Повторная доставка события не создаёт эффекта | `event_id` | R-TRACE-1 |
| Политика не меняется рабочими ролями | защищённые пути | R-PROT-1 |
| Агент не может расширить себе права в запросе | шлюз берёт `allowed_files` из брифа | R-TOOL-1 |
| Вне `RUNNING` инструменты закрыты | шлюз + состояние задачи | R-STATE-1 |
| Обход шлюза виден до коммита | write-set финализатора | R-WRITESET-1 |
| Коммит делает финализатор, журнал едет в нём же | `harness finalize` | R-FINAL-1 |
| «Тесты прошли» подтверждается артефактом | `checks run` в копии дерева | R-CHECK-1 |
| Просроченный диспетч отзывает отдельный процесс | `harness watchdog sweep/serve` | R-TIMEOUT-1 |
| Проверки изолируются, если это возможно | docker без сети, иначе копия с явной пометкой | R-SANDBOX-1 |
| Параллельные задачи не смешивают write-set | `harness lane create/merge` + мьютекс | R-WAVE-1 |

Полный список — `docs/RULES_MATRIX.md` (генерируется, 30 из 32 правил покрыто,
2 пробела названы явно).

## Структура

```
harness/          пакет: cli, fsm, state, policy, hooks, brief, verdict, snapshot,
                  gateway (шлюз инструментов), finalize (коммит), checks (прогон проверок),
                  findings, rules, evals, selftest, demo
policies/         policy.json — полномочия ролей; rules.json — матрица правил
schemas/          JSON-схемы брифа и вердикта
templates/        примеры артефактов
examples/         учебные верификаторы и размеченный набор evals (10 случаев)
tests/            40 unittest-проверок свойств
docs/             ANALYSIS (разбор книги), ARCHITECTURE, ROADMAP, RULES_MATRIX
bin/              обёртки harness / harness.cmd
tools/            вспомогательное: экстрактор текста PDF (для сверки с источником)
```

## Подключение к живому агенту

Роль и задачу выдаёт запускающий процесс, а не агент:

```powershell
$env:HARNESS_ROLE = "worker"; $env:HARNESS_TASK_ID = "FEE-101"
```

Дальше агент работает **только через шлюз** — прямого доступа к файлам у него нет:

```powershell
'{"tool":"write_file","args":{"path":"src/fee.py","content":"..."}}' |
  python -m harness --root . tool --stdin
# {"ok": true, "decision": "allow", "result": {"path": "src/fee.py", "bytes": 42}}

'{"tool":"write_file","args":{"path":"src/billing.py","content":"..."}}' |
  python -m harness --root . tool --stdin
# {"ok": false, "decision": "deny", "rule": "R-WORK-1", "reason": "src/billing.py вне allowed_files брифа"}
```

`allowed_files` и роль шлюз берёт из управляющего процесса; если агент передаст
свой список прав в запросе, он будет проигнорирован (`R-TOOL-1`). Инструменты
закрыты вне состояния `RUNNING` (`R-STATE-1`), просроченный диспетч — тоже.

Готовая обёртка для своего цикла — `examples/harness_client.py`:

```python
from examples.harness_client import HarnessClient, ToolDenied

client = HarnessClient(root=".", role="worker", task_id="FEE-101")
try:
    client.write("src/fee.py", patch)      # внутри — вызов шлюза
except ToolDenied as exc:
    print(exc.rule, exc.reason)            # R-WORK-1: src/fee.py вне allowed_files
client.submit()                            # фиксирует снимок и уходит в VERIFYING
```

Проверки из брифа выполняются в одноразовой копии дерева:

```powershell
python -m harness checks run --task FEE-101 --verdict-out verdict.json
```

Вердикт собирается из фактических кодов возврата; критерий, который не запускался,
получает `skip` и превращает вердикт в `INCONCLUSIVE`.

Коммит делает финализатор, а не агент:

```powershell
python -m harness finalize --task FEE-101 --message "fee: 2.5%"
# проверяет снимок, write-set ⊆ allowed_files, пишет pipeline-log в тот же коммит,
# записывает связь «снимок → коммит»
```

Параллельные задачи работают в отдельных worktree:

```powershell
python -m harness lane create --task FEE-101     # ветка harness/FEE-101
python -m harness lane merge  --task FEE-101     # только COMMITTED, под мьютексом
python -m harness lane list
```

Просроченные диспетчи отзывает отдельный процесс, а не сам агент:

```powershell
python -m harness watchdog sweep                 # один проход (cron/CI)
python -m harness watchdog serve --interval 15   # отдельный цикл
python -m harness sandbox probe                  # что доступно: docker/copy-only
```

При ошибке внутри хука решение — `deny` (`R-HOOK-ERR`): неопределённость закрывает
переход, а не открывает его.

## Что это НЕ защищает

Честный список, чтобы не принять механизм за гарантию:

1. **Обход шлюза напрямую** (прямая запись из своего процесса) — ловится только на
   коммите, через `R-WRITESET-1`; до этого дерево уже изменено.
2. **Запись дочерним процессом** `run_command` — шлюз видит команду, но не её эффекты;
   частично прикрыто write-set и тем, что продукт не коммитится без проверки.
3. **Смысловой дефект**, который проверяющий не заметил — измеряется `evals`, не хуком.
4. **ОС-песочница, сеть и параллельная запись** — не реализованы (`R-SANDBOX-1`, `R-WAVE-1`).
5. **Остановка управляющего процесса** — watchdog живёт в нём же.
6. **Проверки без изоляции прав** — `checks run` даёт копию дерева и таймаут, но не
   отдельного пользователя и не сетевую изоляцию.

## Документация

* [`docs/ANALYSIS.md`](docs/ANALYSIS.md) — что в книге работает, где она переоценивает
  себя и что мы изменили.
* [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — состояния, guard'ы, инварианты,
  артефакты, протокол хука.
* [`docs/ROADMAP.md`](docs/ROADMAP.md) — этапы внедрения и критерии их закрытия.
* [`docs/RULES_MATRIX.md`](docs/RULES_MATRIX.md) — матрица правил (генерируется).

## Evals

```powershell
python -m harness evals run --cases examples/evals/fee --verifier "python examples/verifiers/marker_verifier.py"
python -m harness evals run --cases examples/evals/fee --verifier "python examples/verifiers/behavior_verifier.py"
```

Маркерный верификатор пропускает 3 дефекта из 6 (он смотрит на текст, а не на поведение),
поведенческий — 0 из 6. Это и есть смысл измерения: «верификатор существует» и
«верификатор работает» — разные утверждения.
