# Матрица RULE → HOOK → ENFORCEMENT

_Файл генерируется командой `harness coverage --markdown`. Руками не править: источник — `policies/rules.json` и код хуков._

Всего правил: **32** · покрыто: **28** · пробелов: **0** · делегировано рантайму: **4** · сломанных объявлений: **0**

Проверяется исполняемыми тестами: **28** из **28** наших правил; ещё **4** закрыты механизмами рантайма и плагинами (см. ниже). У каждого нашего правила есть хотя бы одна исполняемая проверка.

> доля покрытия не равна доле безопасности: одна незакрытая критическая операция важнее десятков закрытых малозначимых правил

| Правило | Класс | Событие | Хук | Статус | Владелец |
|---|---|---|---|---|---|
| `R-ROLE-1` | blocking | pretooluse | `policy.role_identity` | покрыто | policy |
| `R-ORCH-1` | blocking | pretooluse | `policy.write_scope` | покрыто | policy |
| `R-VER-1` | blocking | pretooluse | `policy.write_scope` | покрыто | policy |
| `R-WORK-1` | blocking | pretooluse | `policy.worker_scope` | покрыто | policy |
| `R-WORK-2` | blocking | pretooluse | `policy.tool_scope` | покрыто | policy |
| `R-PROT-1` | blocking | pretooluse | `policy.protected_paths` | покрыто | policy |
| `R-BRIEF-1` | blocking | predispatch | `brief.gate_dispatch` | покрыто | briefer |
| `R-GAP-1` | blocking | predispatch | `brief.gate_gap` | покрыто | briefer |
| `R-BUDGET-1` | blocking | predispatch | `fsm.reserve_attempt` | покрыто | control |
| `R-BUDGET-2` | blocking | postverdict | `fsm.apply_verdict` | покрыто | control |
| `R-VERIFY-1` | blocking | precommit | `verdict.require_current_pass` | покрыто | control |
| `R-SNAPSHOT-1` | blocking | precommit | `verdict.require_current_pass` | покрыто | control |
| `R-COMMIT-1` | blocking | precommit | `verdict.require_checks` | покрыто | control |
| `R-COMMIT-2` | blocking | precommit | `verdict.require_pipeline_log` | покрыто | control |
| `R-SLEEP-1` | delegated | pretooluse | `—` | делегировано — закрыто: @deepseek-ai/dsh-tool-call-timeout-policy | policy |
| `R-READ-1` | delegated | pretooluse | `—` | делегировано — закрыто: @deepseek-ai/dsh-fs-observation-policy | policy |
| `R-GIT-1` | delegated | pretooluse | `—` | делегировано — закрыто: dsh-jev-guard (L0, локальные правила, без ключа) | policy |
| `R-TIMEOUT-1` | blocking | watchdog | `watchdog.sweep` | покрыто | control |
| `R-TRACE-1` | blocking | posttransition | `state.append_event` | покрыто | control |
| `R-FIND-1` | blocking | postverdict | `findings.close_guard` | покрыто | control |
| `R-FIND-2` | soft | postverdict | `findings.route` | покрыто | control |
| `R-ADV-1` | blocking | postverdict | `verdict.adversarial_requires_scenario` | покрыто | verifier |
| `R-COST-1` | blocking | predispatch | `cost.check_dispatch` | покрыто | control |
| `R-SANDBOX-1` | delegated | postverdict | `—` | делегировано — закрыто: @deepseek-ai/dsh-sandbox-policy + dsh-pwsh-sandbox | environment |
| `R-HOOK-1` | blocking | process | `rules.change` | покрыто | policy |
| `R-WAVE-1` | blocking | precommit | `lanes.merge` | покрыто | control |
| `R-EVAL-1` | soft | process | `evals.metrics` | покрыто | control |
| `R-STATE-1` | blocking | pretooluse | `gateway.state` | покрыто | control |
| `R-TOOL-1` | blocking | pretooluse | `gateway.tool` | покрыто | control |
| `R-WRITESET-1` | blocking | precommit | `finalize.write_set` | покрыто | control |
| `R-FINAL-1` | blocking | precommit | `finalize.commit` | покрыто | control |
| `R-CHECK-1` | soft | postverdict | `checks.build_verdict` | покрыто | verifier |

## Пробелы покрытия

Пробелов нет (это утверждение стоит перепроверять, а не праздновать).

## Делегировано рантайму и плагинам

- **R-SLEEP-1**: Ожидание вне переходов запрещено: sleep и долгие паузы отклоняются. → `@deepseek-ai/dsh-tool-call-timeout-policy`. Остаточный риск: ожидание не запрещено, а ограничено дедлайном инструмента; вечный цикл остановит таймаут, а не правило
- **R-READ-1**: Третий одинаковый read без промежуточного изменения — отказ (ломалка циклов чтения). → `@deepseek-ai/dsh-fs-observation-policy`. Остаточный риск: read-before-edit и version-guarded write на уровне FS; повторные вызовы ловит @deepseek-ai/dsh-repeat-tool-reminder как предупреждение
- **R-GIT-1**: Деструктивные git-операции обязаны явно называть дерево; работа сохраняется до revert. → `dsh-jev-guard (L0, локальные правила, без ключа)`. Остаточный риск: L0 видит текст команды; обфускация (eval, base64) и «серые» случаи не ловятся — семантический слой платный и требует отказа от локальности
- **R-SANDBOX-1**: Проверки из брифа выполняются в изоляции: контейнер без сети, если доступен, иначе копия дерева с урезанным окружением. → `@deepseek-ai/dsh-sandbox-policy + dsh-pwsh-sandbox`. Остаточный риск: ОС-изоляция режимами read-only/workspace-write/danger-full-access; наш checks.sandbox остаётся запасным путём для headless

## Остаточные риски покрытых правил

- **R-ROLE-1**: Агент с правом писать в свой процесс может подменить переменную окружения; нужна изоляция запуска.
- **R-ORCH-1**: Запись дочерним процессом интерпретатора вне инструмента не видна хуку.
- **R-VER-1**: Запуск проектных тестов с полными правами проверяющего обходит границу (нужен отдельный тест-раннер).
- **R-WORK-1**: Симлинки не разыменовываются: запись через ссылку вне allowed_files технически возможна.
- **R-WORK-2**: git через вложенный shell-скрипт распознаётся эвристикой, не разбором.
- **R-PROT-1**: Права на уровне ОС не ограничены; изоляция только на уровне инструментов.
- **R-BRIEF-1**: Схема проверяет структуру, не смысл; смысл проверяет человек или брифер-агент.
- **R-GAP-1**: Порог «что считать блокирующим» задаётся человеком, автоматически не выводится.
- **R-BUDGET-1**: Прямой вызов исполнителя в обход управляющего процесса счётчик не обновит.
- **R-BUDGET-2**: Счётчик зависит от честности запускающего процесса.
- **R-VERIFY-1**: Проверяющий может пропустить смысловой дефект; это измеряется evals, не хуком.
- **R-SNAPSHOT-1**: Хеш показывает изменение, но не авторство; нужен доверенный источник записи.
- **R-COMMIT-1**: Доверяем отчёту о выполнении команд; независимый тест-раннер — следующий этап.
- **R-COMMIT-2**: Журнал пишет финализатор; это traceability, а не WAL.
- **R-TIMEOUT-1**: Если сторож не запущен, отзыв не произойдёт; состояние при этом сохраняется и разбирается позже.
- **R-TRACE-1**: Журнал локален; вывоз в доверенное хранилище не реализован.
- **R-FIND-1**: Причина может быть формальной; качество разбирает владелец.
- **R-FIND-2**: Предупреждение не блокирует; находка может формально висеть без ответственного.
- **R-ADV-1**: Сценарий может быть выполнен формально; проверяется человеком при разборе.
- **R-COST-1**: Бюджет действует, только если он задан в брифе; оценка попытки — допущение, а не измерение.
- **R-HOOK-1**: Проверка сравнивает отпечаток файла: правка кода хука без правки rules.json ею не ловится.
- **R-WAVE-1**: Лейны разделяют дерево, но не тестовые БД, порты и внешние ресурсы: это настраивается в проекте.
- **R-EVAL-1**: Малый набор меряет крупные проблемы, не частоту редких отказов.
- **R-STATE-1**: Проверка действует только для вызовов через шлюз; прямая запись ловится финализатором.
- **R-TOOL-1**: Агент с доступом к файлу брифа в .harness/ мог бы прочитать его, но не изменить (R-PROT-1).
- **R-WRITESET-1**: Проверка идёт относительно рабочего дерева; грязные файлы, существовавшие до задачи, тоже видны и требуют решения.
- **R-FINAL-1**: Хеш коммита нельзя записать внутрь него самого; связь хранится в состоянии и проверяется постфактум.
- **R-CHECK-1**: Прогон идёт без изоляции сети и прав процесса; верификатор может не использовать раннер.
