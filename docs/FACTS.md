# Факты о проекте

Атомарные утверждения о том, как устроен `harness`. Один пункт — один факт, самодостаточный,
с явным субъектом. Этот файл — **источник правды для памяти агента**, а не документация:
подробности в `docs/ARCHITECTURE.md` и `docs/RULES_MATRIX.md`. Ищется он через xerj
(префикс `harness`, область `docs/FACTS.md`); отдельного сервиса памяти с 27.10.2026 нет —
см. [`XERJ.md`](XERJ.md#7-замер-xerj-против-hindsight-на-одних-и-тех-же-вопросах).

Формат жёсткий: маркированный список, каждый пункт не короче 50 символов.

- Бриф — обязательный артефакт перед запуском задачи: он фиксирует allowed_files, критерии проверки, бюджет попыток и открытые вопросы, а без брифа диспетч не выдаётся (правила R-BRIEF-1 и R-GAP-1).
- Верификатор проверяет снимок кандидата и выносит вердикт PASS, FAIL или INCONCLUSIVE, привязанный к snapshot_id; правка файлов после PASS отменяет вердикт (правило R-SNAPSHOT-1).
- Коммит делает финализатор командой harness finalize, а не агент: он проверяет write-set и актуальность PASS перед коммитом (правила R-VERIFY-1 и R-WRITESET-1).
- На одну задачу даётся не больше трёх попыток; счётчик хранится в SQLite и не сбрасывается сменой агента (правило R-BUDGET-1).
- Шлюз harness tool берёт роль и allowed_files из управляющего процесса, а не из запроса агента: попытка расширить права игнорируется (правило R-TOOL-1).
- Жизненный цикл задачи идёт по 11 состояниям (CLAIMED, BRIEF_READY, RUNNING, VERIFYING, VERIFY_BLOCKED, REWORK_READY, ACCEPTED, COMMITTED, ESCALATED, TIMED_OUT, CANCELLED), и вне состояния RUNNING инструменты закрыты (правило R-STATE-1).
- Плагин harness-guard ставит проверку на ctx.tools.guard() и действует для всех агентов профиля DSH, а шлюз harness tool — переносимая форма для сред без плагинов.
- Lanes дают каждой параллельной задаче отдельный git worktree, а слияние разрешено только для задач в состоянии COMMITTED и выполняется под мьютексом (правило R-WAVE-1).
- Watchdog отзывает просроченные диспетчи отдельным процессом harness watchdog sweep или serve, потому что сам агент этого не сделает (правило R-TIMEOUT-1).
- Evals измеряют качество проверяющего: на наборе fee маркерный верификатор даёт 0 пропусков из 13, 1 ложный отказ из 8 и 16 неполных проверок из 21, поведенческий — 0/13, 0/8 и 0/21.
- Обход шлюза прямой записью виден только на коммите через write-set (правило R-WRITESET-1), а запись дочерним процессом хуку не видна вовсе.
- Все правила проекта живут в policies/rules.json, а docs/RULES_MATRIX.md генерируется из них командой harness coverage --markdown и руками не правится.
- Стоимость проверяется до запуска: budget.max_cost и estimate_per_attempt в брифе останавливают прогон заранее (правило R-COST-1).
- Изменение правила фиксируется командой harness rule change с обязательной причиной, иначе rule verify покажет расхождение отпечатка (правило R-HOOK-1).
- Уровень изменения ведёт changefsm.py: DRAFT → … → READY → IN_PROGRESS → … → ARCHIVED, диспетч задачи по зарегистрированному change разрешён только из READY/IN_PROGRESS (правила R-CHG-1 и R-REF-1).
- Ready-гейт требует review-записи BA, SA, DEV и QA по актуальным хешам proposal/specs/design/tasks, полную трассировку REQ-CASE-task и отсутствие блокирующих GAP (правила R-REV-1, R-REV-2, R-TRACE-2).
- spec_ref в брифе — полный Git SHA плана (40 hex); specgate принимает только живой change и сверяет digest спеки с spec_version (правило R-SPEC-1).
- Схема состояния версии 4: таблицы changes, change_events, change_reviews и колонка tasks.change_id; review-записи дублируются файлами в openspec/changes/<id>/reviews/.
- В матрице 47 правил: 44 наших покрытых, 3 делегированы рантайму; selftest содержит 61 исполняемую проверку, unittest — 434.
- harness metrics считает очередь, lead time Ready → Accept, rework, review, verification, evidence и находки из SQLite и файлов change; harness board --out рендерит самодостаточный HTML со снимком.
- harness ci выполняет selftest, покрытие, метрики и (с --run-tests) unittest, пишет evidence/ci-<ts>/report.json с Git HEAD и возвращает 0/2.
- harness board --serve поднимает локальную HTTP-доску (/, /metrics.json, /healthz) с автообновлением; harness otel отдаёт OTLP/HTTP JSON с метриками процесса.
- tools/headless_smoke.py запускает dsh headless --json и пишет evidence-отчёт; в этой установке final приходит с MISSING_CREDENTIAL, потому что у headless-профиля нет DEEPSEEK_API_KEY (ключ Web-профиля CLI не видит).
- Change может объявлять сервисы: change new --service name=PATH[@SHA]; сервисы попадают в бриф с base_commit и валидируются (относительный путь без .., полный SHA).
- Verification поддерживает несколько сервисов: прогоны пишутся по ключам service:mode, --cwd задаёт каталог сервиса, для каждого объявленного сервиса нужны baseline и candidate.
- Порядок объявления сервисов в change — merge order: release/acceptance обязаны сохранить его, merge_order пишется в записи и evidence; другой порядок отклоняется.
- Мультикорневая запись открыта явно: change new --service-write NAME ставит write=true, бриф использует allowed_files service:<name>/<rel>; policy/guard/gateway требуют can_write_services (worker), write=true и realpath-проверку. Snapshot задачи и finalize понимают writable-сервисы (`add-service-commit`): отдельный коммит сервиса или главный коммит для in-root без git. Shell-запись в сервис не открыта; push/PR сервисов — нет.
- verification.json хранит два прогона (baseline/candidate) с ref, runner, окружением, версиями сервисов и diff падений; нулевой прогон и новые падения блокируют VERIFIED (правила R-QA-1, R-QA-2).
- Для VERIFIED→RELEASED→ACCEPTED→ARCHIVED нужны release.json, acceptance.json, closing.json и evidence/change-<id>/manifest.json с sha256; для L2 by_role и accepted_by_role обязаны быть human (правила R-ACCEPT-1, R-ACCEPT-2).
- harness verification run|show делает baseline/candidate, harness change release|accept|close|evidence записывает выпуск, приёмку и пакет доказательств.
- Процессные роли исполняются пресетами DSH process-ba/sa/dev/qa/verifier, а права проверяет guard: analyst пишет openspec/**, qa — tests/** и cases, worker — только allowed_files (правило R-WORK-3).
- actor_role брифа (BA/SA/DEV/QA/verifier) переводится в роль политики и публикуется в guard-scope.json при диспетче; без поля роль остаётся worker.
- Привилегированные инструменты (plugin_manager, marketplace, mcp__awgm__/telegram/desktop) закрыты рабочим ролям правилом R-TOOL-2; читающие mcp__xerj__ и mcp__trendshift__ разрешены.
- Открытый .harness/gap.json превращает пишущий вызов в ask человеку через tools/pre-execute (правило R-GAP-2); чтение не блокируется, снятие — harness gap clear.
