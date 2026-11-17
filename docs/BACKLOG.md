# Backlog v2

Обновлён 17.11.2026 после deployment v1. Здесь только то, что действительно
осталось; закрытые пункты убраны, а не перечислены «на всякий случай».

## Уже закрыто (не задачи)

- CI: GitHub Actions, unittest + `harness ci` + `rule verify`, artifacts;
- evidence: cosign keyless sign/verify, artifact `harness-ci-evidence`;
- DSH: автодиспетч `agent/created` и opt-in auto-submit `--auto-submit`;
- лейны: `harness/lanes.py` — worktree, слоты/порты/БД, merge под мьютексом,
  конфликт откатывается, а не правится координатором;
- merge order: порядок сервисов записывается и сверяется с планом;
- пять ранних change закрыты как `CANCELLED`;
- пилоты `add-multi-root-write`, `add-service-commit`, `add-session-adapter`,
  `add-adapter-attach`, `add-adapter-autosubmit` — ARCHIVED.

## Волна 1 — корректность и границы (можно делать без новых решений)

1. **`can_read`/`may_*` — исполняемые.** ✅ Закрыто в
   `add-capability-enforcement`: CLI проверяет may_*, хук/guard — can_read.
   Остаётся доверенная идентичность (подмена scope/env) — отдельный пункт.
2. **Push/PR и частичный откат.** ✅ Закрыто в `add-lane-push-rollback`:
   `lane push/pr`, `reset --soft` для сервисных и главного коммита, удаление
   commit-записей. Остаётся push/PR сервисных репозиториев в их remote.
3. **PR-статус и forge.** ✅ Закрыто в `add-forge-status`: `harness forge status`
   читает checks через `gh` (CheckRun и StatusContext), без `gh` — отказ. CI есть.
4. **Внешнее хранилище evidence.** ✅ Закрыто в `add-evidence-export`:
   `evidence export/verify` копирует в указанный каталог с sha256-манифестом.
   Остаётся только облачный API/шифрование (Out of Scope).

## Волна 2 — видимость (нужен дизайн/UI)

5. **OTel traces/spans.** ✅ Закрыто в `add-otel-spans`: spans переходов,
   детерминированный `trace_id`, отдельный OTLP traces-payload. Остаётся
   distributed tracing между задачами (Out of Scope).
6. **Панель в DSH GUI.** ✅ Закрыто в `add-harness-panel`: client-бандл в
   `conversation.composer.dock`, читает `/metrics.json` доски, offline-состояние.
   HTML-доска остаётся для headless/браузера.
7. **Multi-service e2e.** ✅ Закрыто в `add-multi-service-e2e`: прогон на копиях
   сервисов (`@SHA` через `git archive`), env `HARNESS_E2E_*`, отчёт. Остаётся
   merge order как очередь и реальные зависимости (Out of Scope).

## Волна 3 — доверие и подпись (нужны ключи/решение)

8. **Подпись коммитов.** ✅ Закрыто в `add-signed-commits`: SSH-ключ, allowed_signers,
   CI verify диапазона. Остаётся **tamper-proof finalizer** (отдельный пункт).
9. **L2-процесс.** Второй независимый ревьюер и двухключевая приёмка для
   критичных change; требует людей/агентов и бюджета.

## P3 — не «доделать», а вести

10. 20–30 размеченных случаев и калибровка верификатора.
11. Владелец evals, SLO, квартальный разбор, маршрутизация моделей.
12. Классификация данных/PII, секреты, vetting MCP, legal/комплаенс.
13. Решения §9 PROCESS: топология репозиториев, CI-хостинг, что можно отправлять
    модели, владелец пилота, нужен ли GUI.

## Правило выбора

Одна волна — один change с review/verification. Внутри волны можно делать
несколько независимых change, но не смешивать UI, безопасность и инфраструктуру
в один коммит. P3 требует владельца и ритма, а не галочки.
