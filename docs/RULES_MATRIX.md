# Матрица RULE → HOOK → ENFORCEMENT

_Файл генерируется командой `harness coverage --markdown`. Руками не править: источник — `policies/rules.json` и код хуков._

Всего правил: **51** · покрыто: **49** · пробелов: **0** · делегировано рантайму: **2** · сломанных объявлений: **0**

Проверяется исполняемыми тестами: **49** из **49** наших правил; ещё **2** закрыты механизмами рантайма и плагинами (см. ниже). У каждого нашего правила есть хотя бы одна исполняемая проверка.

> доля покрытия не равна доле безопасности: одна незакрытая критическая операция важнее десятков закрытых малозначимых правил

| Правило | Класс | Событие | Хук | Статус | Владелец |
|---|---|---|---|---|---|
| `R-ROLE-1` | blocking | pretooluse | `policy.role_identity` | покрыто | policy |
| `R-CAP-1` | blocking | pretooluse | `policy.capability` | покрыто | policy |
| `R-ROLE-2` | blocking | pretooluse | `scope.write` | покрыто | policy |
| `R-ORCH-1` | blocking | pretooluse | `policy.write_scope` | покрыто | policy |
| `R-VER-1` | blocking | pretooluse | `policy.write_scope` | покрыто | policy |
| `R-WORK-1` | blocking | pretooluse | `policy.worker_scope` | покрыто | policy |
| `R-WORK-2` | blocking | pretooluse | `policy.tool_scope` | покрыто | policy |
| `R-PROT-1` | blocking | pretooluse | `policy.protected_paths` | покрыто | policy |
| `R-BRIEF-1` | blocking | predispatch | `brief.gate_dispatch` | покрыто | briefer |
| `R-BRIEF-2` | blocking | predispatch | `fsm.brief_policy_compat` | покрыто | briefer |
| `R-GAP-1` | blocking | predispatch | `brief.gate_gap` | покрыто | briefer |
| `R-BUDGET-1` | blocking | predispatch | `fsm.reserve_attempt` | покрыто | control |
| `R-BUDGET-2` | blocking | postverdict | `fsm.apply_verdict` | покрыто | control |
| `R-VERIFY-1` | blocking | precommit | `verdict.require_current_pass` | покрыто | control |
| `R-SNAPSHOT-1` | blocking | precommit | `verdict.require_current_pass` | покрыто | control |
| `R-COMMIT-1` | blocking | precommit | `verdict.require_checks` | покрыто | control |
| `R-COMMIT-2` | blocking | precommit | `verdict.require_pipeline_log` | покрыто | control |
| `R-SLEEP-1` | delegated | pretooluse | `—` | делегировано — закрыто: @deepseek-ai/dsh-tool-call-timeout-policy | policy |
| `R-READ-1` | blocking | pretooluse | `policy.read_scope` | покрыто | policy |
| `R-GIT-1` | blocking | pretooluse | `bash.git_destructive` | покрыто | policy |
| `R-TIMEOUT-1` | blocking | watchdog | `watchdog.sweep` | покрыто | control |
| `R-TRACE-1` | blocking | posttransition | `state.append_event` | покрыто | control |
| `R-FIND-1` | blocking | postverdict | `findings.close_guard` | покрыто | control |
| `R-FIND-2` | soft | postverdict | `findings.route` | покрыто | control |
| `R-ADV-1` | blocking | postverdict | `verdict.adversarial_requires_scenario` | покрыто | verifier |
| `R-COST-1` | blocking | predispatch | `cost.check_dispatch` | покрыто | control |
| `R-SANDBOX-1` | delegated | postverdict | `—` | делегировано — закрыто: @deepseek-ai/dsh-sandbox-policy + @deepseek-ai/dsh-pwsh-sandbox | environment |
| `R-HOOK-1` | blocking | process | `rules.change` | покрыто | policy |
| `R-WAVE-1` | blocking | precommit | `lanes.merge` | покрыто | control |
| `R-PUSH-1` | blocking | precommit | `lanes.push` | покрыто | lanes |
| `R-FORGE-1` | blocking | precommit | `forge.status` | покрыто | forge |
| `R-EVID-1` | blocking | precommit | `evidence.export` | покрыто | evidence |
| `R-EVAL-1` | soft | process | `evals.metrics` | покрыто | control |
| `R-STATE-1` | blocking | pretooluse | `gateway.state` | покрыто | control |
| `R-TOOL-1` | blocking | pretooluse | `gateway.tool` | покрыто | control |
| `R-WRITESET-1` | blocking | precommit | `finalize.write_set` | покрыто | control |
| `R-FINAL-1` | blocking | precommit | `finalize.commit` | покрыто | control |
| `R-CHECK-1` | soft | postverdict | `checks.build_verdict` | покрыто | verifier |
| `R-CHG-1` | blocking | predispatch | `changefsm.gate_dispatch` | покрыто | control |
| `R-REF-1` | blocking | predispatch | `changefsm.check_spec_ref` | покрыто | control |
| `R-REV-1` | blocking | process | `changefsm.check_review` | покрыто | control |
| `R-REV-2` | blocking | process | `changefsm.check_reviewer` | покрыто | policy |
| `R-TRACE-2` | blocking | process | `changefsm.check_trace` | покрыто | control |
| `R-WORK-3` | blocking | pretooluse | `policy.write_scope` | покрыто | policy |
| `R-TOOL-2` | blocking | pretooluse | `policy.tool_scope` | покрыто | policy |
| `R-GAP-2` | blocking | pretooluse | `gap.decide` | покрыто | control |
| `R-QA-1` | blocking | process | `changefsm.verification_check` | покрыто | control |
| `R-QA-2` | blocking | process | `verification.check_zero` | покрыто | control |
| `R-ACCEPT-1` | blocking | process | `changefsm.archive_check` | покрыто | control |
| `R-ACCEPT-2` | blocking | process | `changefsm.release_check` | покрыто | policy |
| `R-SPEC-1` | blocking | precommit | `specgate.check` | покрыто | control |

## Пробелы покрытия

Пробелов нет (это утверждение стоит перепроверять, а не праздновать).

## Делегировано рантайму и плагинам

- **R-SLEEP-1**: Ожидание вне переходов запрещено: sleep и долгие паузы отклоняются. → `@deepseek-ai/dsh-tool-call-timeout-policy`. Остаточный риск: ожидание не запрещено, а ограничено дедлайном инструмента; вечный цикл остановит таймаут, а не правило
- **R-SANDBOX-1**: Проверки из брифа выполняются в изоляции: контейнер без сети, если доступен, иначе копия дерева с урезанным окружением. → `@deepseek-ai/dsh-sandbox-policy + @deepseek-ai/dsh-pwsh-sandbox`. Остаточный риск: ОС-изоляция режимами read-only/workspace-write/danger-full-access; наш checks.sandbox остаётся запасным путём для headless

## Остаточные риски покрытых правил

- **R-ROLE-1**: Агент с правом писать в свой процесс может подменить переменную окружения; нужна изоляция запуска.
- **R-CAP-1**: роль берётся из guard-scope/env; подмена процесса с большими правами не ловится
- **R-ROLE-2**: Идентификация опирается на agent.id и поле primary в области прав; при отмене задачи дочерний агент без scope распознаётся по exec.parent, а не по идентификатору.
- **R-ORCH-1**: Запись дочерним процессом интерпретатора вне инструмента не видна хуку.
- **R-VER-1**: Запуск проектных тестов с полными правами проверяющего обходит границу (нужен отдельный тест-раннер).
- **R-WORK-1**: Guard/gateway делают realpath; остаётся TOCTOU между проверкой и записью, а shell-обход контролируется эвристикой.
- **R-WORK-2**: git через вложенный shell-скрипт распознаётся эвристикой, не разбором.
- **R-PROT-1**: Права на уровне ОС не ограничены; изоляция только на уровне инструментов.
- **R-BRIEF-1**: Схема проверяет структуру, не смысл; смысл проверяет человек или брифер-агент.
- **R-BRIEF-2**: Проверяется только доступность путей политикой; смысл задачи и объём работы по-прежнему не проверяются.
- **R-GAP-1**: Порог «что считать блокирующим» задаётся человеком, автоматически не выводится.
- **R-BUDGET-1**: Прямой вызов исполнителя в обход управляющего процесса счётчик не обновит.
- **R-BUDGET-2**: Счётчик зависит от честности запускающего процесса.
- **R-VERIFY-1**: Проверяющий может пропустить смысловой дефект; это измеряется evals, не хуком.
- **R-SNAPSHOT-1**: Хеш показывает изменение, но не авторство; нужен доверенный источник записи.
- **R-COMMIT-1**: Доверяем отчёту о выполнении команд; независимый тест-раннер — следующий этап.
- **R-COMMIT-2**: Журнал пишет финализатор; это traceability, а не WAL.
- **R-READ-1**: bash/python -c может читать вне can_read; хук ловит только инструменты чтения, не произвольный код
- **R-GIT-1**: ловится текст команды: обфускация (eval, base64, алиасы) и команды, собранные из переменных, проходят мимо; откат уже случившегося не предусмотрен
- **R-TIMEOUT-1**: Если сторож не запущен, отзыв не произойдёт; состояние при этом сохраняется и разбирается позже.
- **R-TRACE-1**: Журнал локален; вывоз в доверенное хранилище не реализован.
- **R-FIND-1**: Причина может быть формальной; качество разбирает владелец.
- **R-FIND-2**: Предупреждение не блокирует; находка может формально висеть без ответственного.
- **R-ADV-1**: Сценарий может быть выполнен формально; проверяется человеком при разборе.
- **R-COST-1**: Бюджет действует, только если он задан в брифе; оценка попытки — допущение, а не измерение.
- **R-HOOK-1**: Проверка сравнивает отпечаток файла: правка кода хука без правки rules.json ею не ловится.
- **R-WAVE-1**: Лейн получает слот с базой портов, тестовой БД и временным каталогом; остаток — сеть, внешние сервисы и общие очереди, которые harness не поднимает.
- **R-PUSH-1**: push — внешняя публикация кода; non-fast-forward и права remote не отменяются
- **R-FORGE-1**: gh/API может отдать неполный statusCheckRollup; branch protection недоступна
- **R-EVID-1**: доверенность каталога — ответственность оператора; облачный SDK не используется
- **R-EVAL-1**: Малый набор меряет крупные проблемы, не частоту редких отказов.
- **R-STATE-1**: Проверка действует только для вызовов через шлюз; прямая запись ловится финализатором.
- **R-TOOL-1**: Агент с доступом к файлу брифа в .harness/ мог бы прочитать его, но не изменить (R-PROT-1).
- **R-WRITESET-1**: Проверка идёт относительно рабочего дерева; грязные файлы, существовавшие до задачи, тоже видны и требуют решения.
- **R-FINAL-1**: Хеш коммита нельзя записать внутрь него самого; связь хранится в состоянии и проверяется постфактум.
- **R-CHECK-1**: Прогон идёт без изоляции сети и прав процесса; верификатор может не использовать раннер.
- **R-CHG-1**: Состояние живёт в локальной БД: обход harness и прямой запуск исполнителя не видят гейта.
- **R-REF-1**: SHA подтверждает версию плана, но не соответствие кода спеке; это проверяет верификатор.
- **R-REV-1**: Запись можно заполнить формально: критерии не доказывают глубину проверки, это измеряют evals и человек.
- **R-REV-2**: Идентификаторы задаёт вызывающий; при подмене личности проверка не сработает — нужна доверенная идентификация.
- **R-TRACE-2**: Трассировка проверяет наличие связи, а не её смысл: сценарий может не проверять требование по существу.
- **R-WORK-3**: Проверка держится на глобе путей; переименование каталога спеки требует правки политики.
- **R-TOOL-2**: Список префиксов проектный: новый MCP-сервер с побочными эффектами надо внести в privileged_prefixes.
- **R-GAP-2**: Подагенту ask запрещён рантаймом: вопрос из воркера должен возвращаться родителю; при отказе вызов закрывается, а не проходит.
- **R-QA-1**: Версии и окружение записывает тот, кто запускает прогон; подлог ловится только сверкой с release и acceptance.
- **R-QA-2**: Парсер считает строки вида «Ran N tests»; нестандартный runner может не назвать число — тогда отчёт честно не проходит.
- **R-ACCEPT-1**: Записи — файлы в change; их можно подделать, но они связаны spec_ref и версиями сервисов.
- **R-ACCEPT-2**: Роль записывается строкой; подмена идентичности ловится только доверенной идентификацией.
- **R-SPEC-1**: Правило проверяет связь брифа со спекой, а не соответствие кода спеке: смысловое расхождение ловит верификатор.
