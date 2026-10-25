# Матрица RULE → HOOK → ENFORCEMENT

_Файл генерируется командой `harness coverage --markdown`. Руками не править: источник — `policies/rules.json` и код хуков._

Всего правил: **32** · покрыто: **32** · пробелов: **0** · сломанных объявлений: **0**

Проверяется исполняемыми тестами: **32** из **32**. У каждого правила есть хотя бы одна исполняемая проверка.

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
| `R-SLEEP-1` | blocking | pretooluse | `bash.no_sleep` | покрыто | policy |
| `R-READ-1` | blocking | pretooluse | `bash.read_loop` | покрыто | policy |
| `R-GIT-1` | blocking | pretooluse | `bash.git_destructive` | покрыто | policy |
| `R-TIMEOUT-1` | blocking | watchdog | `watchdog.sweep` | покрыто | control |
| `R-TRACE-1` | blocking | posttransition | `state.append_event` | покрыто | control |
| `R-FIND-1` | blocking | postverdict | `findings.close_guard` | покрыто | control |
| `R-FIND-2` | soft | postverdict | `findings.route` | покрыто | control |
| `R-ADV-1` | blocking | postverdict | `verdict.adversarial_requires_scenario` | покрыто | verifier |
| `R-COST-1` | blocking | predispatch | `cost.check_dispatch` | покрыто | control |
| `R-SANDBOX-1` | soft | postverdict | `checks.sandbox` | покрыто | environment |
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
- **R-SLEEP-1**: Обход через интерпретатор или фоновый процесс не ловится эвристикой.
- **R-READ-1**: Счётчик хранится в .harness/read_counter.json и сбрасывается изменением файла, то есть реальным прогрессом, а не перезапуском.
- **R-GIT-1**: Эвристика по подкомандам; алиасы и обёртки не распознаются.
- **R-TIMEOUT-1**: Если сторож не запущен, отзыв не произойдёт; состояние при этом сохраняется и разбирается позже.
- **R-TRACE-1**: Журнал локален; вывоз в доверенное хранилище не реализован.
- **R-FIND-1**: Причина может быть формальной; качество разбирает владелец.
- **R-FIND-2**: Предупреждение не блокирует; находка может формально висеть без ответственного.
- **R-ADV-1**: Сценарий может быть выполнен формально; проверяется человеком при разборе.
- **R-COST-1**: Бюджет действует, только если он задан в брифе; оценка попытки — допущение, а не измерение.
- **R-SANDBOX-1**: При отсутствии docker остаётся только копия дерева: права процесса и сеть не ограничены.
- **R-HOOK-1**: Проверка сравнивает отпечаток файла: правка кода хука без правки rules.json ею не ловится.
- **R-WAVE-1**: Лейны разделяют дерево, но не тестовые БД, порты и внешние ресурсы: это настраивается в проекте.
- **R-EVAL-1**: Малый набор меряет крупные проблемы, не частоту редких отказов.
- **R-STATE-1**: Проверка действует только для вызовов через шлюз; прямая запись ловится финализатором.
- **R-TOOL-1**: Агент с доступом к файлу брифа в .harness/ мог бы прочитать его, но не изменить (R-PROT-1).
- **R-WRITESET-1**: Проверка идёт относительно рабочего дерева; грязные файлы, существовавшие до задачи, тоже видны и требуют решения.
- **R-FINAL-1**: Хеш коммита нельзя записать внутрь него самого; связь хранится в состоянии и проверяется постфактум.
- **R-CHECK-1**: Прогон идёт без изоляции сети и прав процесса; верификатор может не использовать раннер.
