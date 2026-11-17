# Design: исполняемые границы ролей

## Decisions

**1. Роль — из процесса, не из аргумента.**
CLI берёт `active_role(root)` (guard-scope), затем `HARNESS_ROLE`, затем `human`.
Это тот же источник, что у attach; агент не может повысить роль аргументом.

**2. may_* — в точке действия.**
Проверка стоит в `cmd_dispatch`, `cmd_commit`, `cmd_verdict`, `cmd_finalize` до
создания Engine: отказ дешёвый и не меняет состояние. `orchestrator` сохраняет
dispatch/commit, `verifier` — verify, worker — ничего из этого.

**3. can_read — в хуке и guard.**
Python-хук `policy.read_scope` и JS `checkRead` используют один и тот же
`can_read` из policy.json; проверяются `read_file/glob/grep/list_dir`, в том числе
вызов без пути при ограниченном can_read. R-READ-1 в реестре переведён из
delegated в blocking (прежняя delegated-запись про repeat-read больше не
действует). Сейчас у всех `**`, поэтому внутри проекта поведение не меняется;
чтение вне корня теперь отказывается — это осознанное сужение.

**4. fail-closed.**
В Python ошибка хука — отказ. В JS-guard: если политика не найдена, сессия
только осматривается (чтение разрешено), но если корень найден и политика
сломана — отказ `R-HOOK-ERR`; запись/команды запрещены всегда.

## Risks

- `guard-scope`/env всё ещё можно подменить процессом с большими правами;
  доверенная идентичность — отдельный пункт.
- `bash`/`python -c` может читать вне can_read; хук ловит только инструменты
  чтения, не произвольный код.
- `cmd_run` проверяет dispatch (+commit при `--commit`), а verifier-команду может
  запускать только human/orchestrator/system; task_new/brief_attach не ролевые.
- Прочие state-mutating команды (`change review/ready/accept/release/close`,
  `task new`, `brief attach`, watchdog/timed-event) пока без may_* — это
  отдельный change; здесь они не названы «исполняемыми».
- Чтение сервиса по ключу `service:<name>/...` тоже подчиняется can_read; при
  ужесточении политики открытый сервис перестанет читаться без глоба.
