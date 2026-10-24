# harness — управляемый агентный цикл

Реализация практик из учебника «Агентные пайплайны» (Антон / Астра, издание 1):
роли с ограниченными правами, обязательный бриф, независимая проверка с вердиктом-объектом,
бюджет попыток, автомат состояний, матрица `RULE → HOOK → ENFORCEMENT` и измерение
самого контроля.

Зависимостей нет — только Python 3.10+ и стандартная библиотека.

## Быстрый старт

```powershell
python -m harness selftest        # 31 исполняемая проверка правил (отказ воспроизводится)
python -m harness demo            # сквозной цикл: PASS, FAIL, бюджет, watchdog, отказы
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

Полный список — `docs/RULES_MATRIX.md` (генерируется, 23 из 27 правил покрыто,
4 пробела названы явно).

## Структура

```
harness/          пакет: cli, fsm, state, policy, hooks, brief, verdict, snapshot,
                  findings, rules, evals, selftest, demo
policies/         policy.json — полномочия ролей; rules.json — матрица правил
schemas/          JSON-схемы брифа и вердикта
templates/        примеры артефактов
examples/         учебные верификаторы и размеченный набор evals (10 случаев)
tests/            30 unittest-проверок свойств
docs/             ANALYSIS (разбор книги), ARCHITECTURE, ROADMAP, RULES_MATRIX
bin/              обёртки harness / harness.cmd
tools/            вспомогательное: экстрактор текста PDF (для сверки с источником)
```

## Подключение к живому агенту

Роль выдаёт запускающий процесс, а не агент:

```powershell
$env:HARNESS_ROLE = "worker"; $env:HARNESS_TASK_ID = "FEE-101"
```

Каждый вызов инструмента проходит через хук; контекст — JSON на stdin:

```powershell
'{"event":"pretooluse","tool":"write_file","role":"worker","path":"src/fee.py","allowed_files":["src/fee.py"]}' |
  python -m harness --root . hook pretooluse --stdin
# exit 0 — можно, exit 2 — deny (с правилом и причиной)
```

При ошибке внутри хука решение — `deny` (`R-HOOK-ERR`): неопределённость закрывает
переход, а не открывает его.

## Что это НЕ защищает

Честный список, чтобы не принять механизм за гарантию:

1. Прямой вызов исполнителя в обход `harness` — счётчик и гейты не сработают.
2. Запись дочерним процессом интерпретатора — хук видит команду, но не её эффекты.
3. Смысловой дефект, который проверяющий не заметил — это измеряется `evals`, а не хуком.
4. ОС-песочница и параллельная запись — не реализованы (`R-SANDBOX-1`, `R-WAVE-1`).
5. Остановка самого управляющего процесса — watchdog живёт в нём же.

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
