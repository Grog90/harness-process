# harness — управляемый агентный цикл

[![ci](https://github.com/Grog90/harness-process/actions/workflows/ci.yml/badge.svg)](https://github.com/Grog90/harness-process/actions/workflows/ci.yml)

Реализация практик из учебника «Агентные пайплайны» (Антон / Астра, издание 1):
роли с ограниченными правами, обязательный бриф, независимая проверка с вердиктом-объектом,
бюджет попыток, автомат состояний, матрица `RULE → HOOK → ENFORCEMENT` и измерение
самого контроля.

Зависимостей нет — только Python 3.10+ и стандартная библиотека.

## Быстрый старт

```powershell
python -m harness selftest        # исполняемые проверки правил
python -m harness demo            # сквозной цикл, 50 проверок
python -m harness coverage        # покрытие, пробелы и наличие тестов
python -m unittest discover -s tests -v
```

Запуск задачи целиком — одной командой (исполнитель и проверяющий — любые команды):

```powershell
python -m harness run --task FEE-101 --brief templates/brief.example.json `
  --worker   "python examples/agents/demo_worker.py" `
  --verifier "python examples/verifiers/behavior_verifier.py" `
  --commit
```

Пошагово (для отладки и ручного контроля):

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

Подробности запуска: [`docs/RUNNING.md`](docs/RUNNING.md) — контракты команд
воркера и проверяющего, режимы (`--lane`, `--merge`, `--adversarial`), что делать,
когда прогон остановился.

Любой отказ печатает правило и причину и возвращает код выхода `2`:

```
ОТКАЗ [R-SNAPSHOT-1] кандидат изменился после PASS: коммит запрещён, требуется повторная проверка
```

## Уровень изменения

Задача отвечает на вопрос «сделай этот кусок», изменение — «что и зачем делаем и кто
это согласовал». Уровень изменения включается, когда change зарегистрирован в
состоянии; брифы без `change_id` продолжают работать как раньше.

```powershell
python -m harness change new --change add-change-layer --owner human --risk L1
python -m harness change review --change add-change-layer --artifact specs `
  --reviewer ba-1 --role BA --author ba-agent --criterion "правила и примеры"
python -m harness change trace --change add-change-layer
python -m harness change advance --change add-change-layer --event explore
python -m harness change advance --change add-change-layer --event propose
python -m harness change advance --change add-change-layer --event agree_specs
python -m harness change advance --change add-change-layer --event agree_design
python -m harness change ready --change add-change-layer --spec-ref <полный SHA 40 hex>
python -m harness change show  --change add-change-layer
```

`ready` проверяет не «файлы на месте», а доказательства: review-записи всех ролей по
актуальным хешам артефактов, трассировку `REQ → CASE → task`, отсутствие блокирующих
GAP и полный `spec_ref`. Диспетч задачи по зарегистрированному change разрешён только
из `READY`/`IN_PROGRESS`; первый диспетч переводит change в `IN_PROGRESS`.
`brief from-change` заполняет `change_id`, `spec_ref` и для каждого критерия —
`req_id`/`case_id`; поэтому change нужно зарегистрировать (`change new`) до диспетча,
иначе guard откажет по `R-CHG-1`. Брифы без `change_id` работают по-старому.

## Процессные роли

Роли BA/SA/DEV/QA/verifier исполняются пресетами DSH (`plugins/process-presets`), а
права на пути и инструменты проверяет guard по `policies/policy.json`:

* BA/SA — роль `analyst`: пишут только `openspec/**` и документы;
* DEV — `worker`: только `allowed_files`, спеку не правит;
* QA — `qa`: только `tests/**`, кейсы и evidence, код сервиса не меняет;
* verifier — только `evidence/` и чтение продукта.

Процессная роль задаётся в брифе (`harness brief from-change --actor-role BA`) и
попадает в область прав при диспетче. Привилегированные инструменты
(`plugin_manager`, marketplace, `mcp__awgm__`/`telegram`/`desktop`) рабочим ролям
закрыты; читающие MCP (`xerj`, `trendshift`) разрешены.

Неоднозначность во время работы публикуется как GAP и превращает пишущий вызов в
вопрос человеку:

```powershell
python -m harness gap ask --task T-1 --question "Отменять оплаченный заказ?"
python -m harness gap show
python -m harness gap clear
```

Скиллы процесса лежат в `skills/process-*` и подключаются пресетами через
`skill-filesystem`; при переносе проекта путь в патче пресетов нужно обновить.

## Проверка, приёмка и закрытие

`VERIFIED` требует не «тесты прошли», а два прогона с версиями:

```powershell
python -m harness verification run --change add-x --mode baseline `
  --ref <base-commit> --command "python -m unittest discover -s tests" `
  --runner unittest --environment staging --service repo=<полный SHA>
python -m harness verification run --change add-x --mode candidate `
  --command "python -m unittest discover -s tests" `
  --runner unittest --environment staging --service repo=<полный SHA>
python -m harness verification show --change add-x
```

`verification.json` хранит оба прогона, `ref`, окружение, runner, число тестов и
diff падений; нулевой прогон и новые падения блокируют `verify`. Дальше:

```powershell
python -m harness change release --change add-x --environment staging `
  --service repo=<SHA> --by human
python -m harness change accept --change add-x --accepted-by owner `
  --criterion "бизнес-результат подтверждён" --environment staging --service repo=<SHA>
python -m harness change close --change add-x --by owner `
  --specs-synced --cases-promoted --contracts-promoted
```

`release.json`, `acceptance.json`, `closing.json` лежат в каталоге change; `close`
собирает `evidence/change-<id>/manifest.json` (sha256 по каждому файлу). Без
приёмки и evidence архив запрещён; для L2 `by_role`/`accepted_by_role` должны быть
`human`.

## Мультисервисные изменения

Change может объявить сервисы и их принятые версии:

```powershell
python -m harness --json change new --change add-x --owner human `
  --service api=../api@<полный SHA> --service web=../web@<полный SHA>
python -m harness change services --change add-x
```

Сервисы попадают в бриф (`services`: имя, путь, `base_commit`) и валидируются: имя и
путь не пустые, `base_commit` — полный SHA. Путь сервиса — ссылка для чтения и прогонов,
а не граница записи: писать в него через guard нельзя.

Verification прогоняется по каталогу сервиса:

```powershell
python -m harness verification run --change add-x --mode baseline `
  --cwd ../api --service api=<SHA> --ref <base> `
  --command "python -m unittest discover -s tests" --runner unittest --environment staging
python -m harness verification run --change add-x --mode candidate `
  --cwd ../api --service api=<SHA> `
  --command "python -m unittest discover -s tests" --runner unittest --environment staging
```

Для каждого объявленного сервиса нужны оба режима; новые падения считаются отдельно.
Порядок `--service` при создании change — это **merge order**: release/acceptance
обязаны сохранить тот же порядок, иначе запись отклоняется; `merge_order` попадает в
`release.json`, `acceptance.json` и evidence. Release/acceptance принимают несколько
`--service name=SHA`, поэтому архив получает несколько версий.

**Граница v1:** по умолчанию сервис read-only. Чтобы worker мог писать в него,
сервис открывается явно при создании change:

```powershell
python -m harness --json change new --change add-x --owner human `
  --service api=../api@<SHA> --service-write api
```

В брифе тогда допустим `allowed_files = ["service:api/src/a.py"]`; worker пишет
только этот файл, остальные роли и сервисы — отказ. Сервисный корень не может
совпадать с главным корнем или быть его предком; если сервис лежит внутри проекта,
его файлы дополнительно проверяются по главному пути (`policies/**` и protected не
обходятся). Shell-запись в сервис (`>>`, `sed -i`) не открывается: писать надо
`write_file`. Снимок задачи и `finalize` понимают `service:`-ключи: `snapshot`
считает деревья writable-сервисов, а финализатор коммитит каждый сервисный репозиторий
отдельно и пишет SHA в evidence. Push/PR в сервисный репозиторий остаётся вне процесса.

## Доска, метрики и CI

```powershell
python -m harness metrics                    # markdown в stdout
python -m harness --json metrics             # машинный снимок
python -m harness metrics --markdown docs/status.md
python -m harness board --out board.html     # самодостаточный HTML-снимок
python -m harness board --serve --port 8765  # живая доска по HTTP
python -m harness otel --out evidence/otlp.json  # OTLP/HTTP JSON
python -m harness otel --endpoint http://127.0.0.1:4318/v1/metrics
python -m harness ci --run-tests             # selftest + покрытие + метрики + тесты
```

Метрики читаются из той же SQLite и файлов change, что и гейты: lead time
Ready → Accept, rework rate, review-записи, состояние verification, evidence,
открытые находки. `--serve` перечитывает данные на каждый запрос и отдаёт `/`,
`/metrics.json`, `/healthz`; это локальная доска, а не встроенная панель DSH GUI.
`otel` отдаёт OTLP/HTTP JSON с метриками процесса (агентские трейсы экспортирует DSH).
`harness ci` пишет отчёт в `evidence/ci-<ts>/report.json` и возвращает 0/2.
Подробности, включая проверку `dsh-headless`, — [docs/CI.md](docs/CI.md).

## Что уже держится механически

| Свойство | Как обеспечено | Проверка |
|---|---|---|
| Оркестратор не пишет код, воркер не выходит за `allowed_files` | политика ролей + хуки | `selftest` R-ORCH-1, R-WORK-1 |
| Задача по change не запускается до Ready | change-FSM + guard `dispatch` | R-CHG-1 |
| Бриф и коммит привязаны к полному `spec_ref` | валидатор брифа + `specgate` | R-REF-1, R-SPEC-1 |
| Ready требует review-записи и трассировку | `changefsm.ready_check` | R-REV-1, R-REV-2, R-TRACE-2 |
| BA/SA не пишут код, DEV не правит спеку | роли `analyst`/`qa` + guard | R-WORK-3 |
| Рабочий не ставит плагины и не трогает опасные MCP | `privileged_tools` + guard | R-TOOL-2 |
| Открытый GAP спрашивает человека до записи | `gap.py` + `tools/pre-execute` | R-GAP-2 |
| Проверка требует baseline/candidate и версий | `verification.py` + гейт `verify` | R-QA-1, R-QA-2 |
| Выпуск/приёмка/архив требуют записей и evidence | `release/accept/close` + манифест | R-ACCEPT-1, R-ACCEPT-2 |
| Очередь и метрики видны без логов | `metrics.py`, `board`, `ci` | — (проекция) |
| Change объявляет несколько сервисов и их версий | `change --service`, `verification --cwd` | — (v1: без записи в чужие корни) |
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
| Запуск останавливается по бюджету до траты | `cost.check_dispatch` в guard диспетча | R-COST-1 |
| Правка правил без причины и тестов видна | `harness rule verify` (отпечаток файла) | R-HOOK-1 |
| Код не коммитится без связанной спеки | `specgate.check` в финализаторе | R-SPEC-1 |

Полный список — `docs/RULES_MATRIX.md` (генерируется). Из 47 правил 44 держим мы
(у каждого исполняемая проверка в `selftest`), 3 делегированы механизмам рантайма —
это не «пробел» и не «покрыто нами», а отдельный статус: см. `docs/PLUGINS.md`.
Это про наличие механизма, а не про его безошибочность: остаточные риски
перечислены там же.

## Структура

```
harness/          пакет: cli, changefsm, fsm, state, policy, hooks, brief, verdict, snapshot,
                  gateway (шлюз), finalize (коммит), checks (прогон проверок),
                  lanes (worktree), watchdog, cost, rulechange, scope, run, pluginctl, doctor, findings,
                  rules, evals, selftest, demo
AGENTS.md         контракт для агента: жёсткие правила и процесс уроков
journal/          журнал ошибок (ступень 1) — продвигается tools/lessons.py
policies/         policy.json — полномочия ролей; rules.json — матрица правил
schemas/          JSON-схемы брифа и вердикта
templates/        примеры артефактов
examples/         учебные агент, верификаторы и размеченный набор evals
plugins/          harness-guard — host-плагин DSH: ограждение на ctx.tools.guard();
                  harness-session — контекст задачи и read-only статус в DSH
tests/            486 unittest-проверки свойств, включая паритет плагина и ядра
docs/             ANALYSIS, ARCHITECTURE, PROCESS, ROADMAP, RUNNING, PLUGIN, PLUGINS, LESSONS, SKILLS, PROMPT-DEFENSE, RECALL, TRENDSHIFT, BROWSER, XERJ, RULES_MATRIX, CI, DEPLOY
bin/              обёртки harness / harness.cmd
tools/            вспомогательное: экстрактор текста PDF, журнал уроков (lessons.py),
                  поиск по прошлым сессиям (recall.py), тренды GitHub (trendshift.py),
                  браузер для агента (browser_mcp.py), MCP-протокол (mcp_stdio.py)
```

## Две формы ограждения

| Форма | Кто подчиняется | Когда использовать |
|---|---|---|
| Плагин `plugins/harness-guard` | **все** агенты профиля DSH: guard стоит на пути инструментов | работа внутри DSH |
| Шлюз `harness tool` / `HarnessClient` | только агент, который сам ходит через шлюз | headless-прогоны, CI, другие рантаймы |

Плагин — сильная форма: агент не может его обойти. Шлюз — переносимая: работает
там, где нет плагинной системы. Обе опираются на один и тот же
`policies/policy.json`; расхождение решений ловит `tests/test_parity.py`.
Подробности — [`docs/PLUGIN.md`](docs/PLUGIN.md).

## Адаптер сессии

`plugins/harness-session` добавляет активную задачу harness в системный контекст
DSH-сессии и даёт read-only инструмент `harness_task`. Он **не** диспетчит и не
выносит вердикт: гейты остаются у управляющего процесса. В профиле `desktop` он
установлен; в поставку harness не входит.

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

Стоимость и дисциплина правил:

```powershell
python -m harness cost record --task FEE-101 --amount 3.5 --note "попытка 1"
python -m harness cost report
python -m harness rule verify                    # правила менялись без записи?
python -m harness rule change --id R-COST-1 --reason "бюджет проверяется до запуска"
```

Бюджет задаётся в брифе (`control.budget`), и запуск останавливается **до** траты:

```json
"control": {"budget": {"max_cost": 12, "estimate_per_attempt": 4}}
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
* [`docs/PROCESS.md`](docs/PROCESS.md) — процесс от бизнес-задачи до приёмки: разбор
  OpenSpec-презентации и учебника по пунктам, двухуровневая архитектура (изменение +
  задача), роли, артефакты, гейты, автоматизация DSH и план внедрения.
* [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — состояния, guard'ы, инварианты,
  артефакты, протокол хука.
* [`docs/ROADMAP.md`](docs/ROADMAP.md) — этапы внедрения и критерии их закрытия.
* [`docs/LESSONS.md`](docs/LESSONS.md) — как ошибка превращается в правило и проверку.
* [`docs/SKILLS.md`](docs/SKILLS.md) — как писать скиллы и агентские инструкции: цена строки, иерархия, критерий готовности, контракт фронтматтера.
* [`docs/PROMPT-DEFENSE.md`](docs/PROMPT-DEFENSE.md) — что считать подозрительным в чужом тексте: признаки и порядок действий.
* [`docs/RECALL.md`](docs/RECALL.md) — поиск по прошлым сессиям: формат, границы, сравнение с Hindsight.
* [`docs/FACTS.md`](docs/FACTS.md) — атомарные факты о проекте: корм для памяти агента.
* [`docs/MEMORY_COMPARE.md`](docs/MEMORY_COMPARE.md) — три прогона сравнения recall и Hindsight, что чинило качество.
* [`docs/TRENDSHIFT.md`](docs/TRENDSHIFT.md) — тренды GitHub: доступ к данным, MCP-сервер, что брать в контур.
* [`docs/BROWSER.md`](docs/BROWSER.md) — браузер для агента: moli, MCP-обёртка, границы и запасной путь через Chrome.
* [`docs/XERJ.md`](docs/XERJ.md) — поиск по коду и докам: установка, индексация, отличие от Hindsight и recall.
* [`docs/TRENDSHIFT.md`](docs/TRENDSHIFT.md) — тренды GitHub: доступ к данным, MCP-сервер, что брать в контур.
  Разбор кандидатов живёт отдельным workspace: `..\trend-lab` (очередь, карточки, скилл `trend-triage`).
* [`docs/RULES_MATRIX.md`](docs/RULES_MATRIX.md) — матрица правил (генерируется).

## Evals

```powershell
python -m harness evals run --cases examples/evals/fee --verifier "python examples/verifiers/marker_verifier.py"
python -m harness evals run --cases examples/evals/fee --verifier "python examples/verifiers/behavior_verifier.py"
```

Маркерный верификатор смотрит на текст и поэтому даёт и пропуски, и ложные отказы, и
неполные проверки; поведенческий на том же наборе — ни одного пропуска. Это и есть
смысл измерения: «верификатор существует» и «верификатор работает» — разные утверждения.

