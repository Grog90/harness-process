# Proposal: приёмка и закрытие изменения

## Why

Change-FSM умеет дойти до VERIFIED, но дальше начинается «почти готово»: нет
доказательства, что проверено принятое и изменённое поведение, выпуск и приёмка
не записаны, архив не требует ни приёмки, ни пакета доказательств. В итоге
`ARCHIVED` можно получить, ничего не подтвердив.

Этап 3 закрывает хвост: два прогона (baseline/candidate) с версиями, отчёт,
записи release/acceptance/closing и пакет evidence с хеш-манифестом. Гейты
`VERIFIED → RELEASED → ACCEPTED → ARCHIVED` становятся проверяемыми.

## What Changes

- `harness/verification.py`: baseline/candidate-прогоны, разбор вывода, diff новых
  падений, `verification.json`/`.md` с версиями.
- `changefsm`: проверки `verify/release/accept/archive`, `release.json`,
  `acceptance.json`, `closing.json`, `write_evidence` с sha256-манифестом.
- CLI: `harness verification run|show`, `harness change release|accept|close|evidence`.
- Правила R-QA-1, R-QA-2, R-ACCEPT-1, R-ACCEPT-2 и исполняемые проверки.

## Impact

- `verify` без отчёта, нулевого прогона, без версий или с новым падением — отказ.
- `release` и `accept` без записей — отказ; L2 требует `by_role=human`.
- `archive` без `acceptance.json`, `closing.json` и evidence-манифеста — отказ.
- L0 может принять без release (осознанное исключение, записано в проверке).

## Out of Scope

- Автоматическое развёртывание в среду: release-запись фиксирует факт, а не деплой.
- Мультирепозиторность и UI (Этапы 4–5).
- Подпись evidence (cosign) — следующий шаг.
