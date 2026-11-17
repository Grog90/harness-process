# Статус процесса v1

Дата: 2026-11-16.

## Что объявлено готовым

Процесс уровня изменения (Этапы 0–5 исходного плана) реализован как **v1** и впервые
проведён на собственном изменении, а не только покрыт тестами.

| Слой | Состояние |
|---|---|
| Change-FSM, review-записи, trace, Ready | готово |
| Роли BA/SA/DEV/QA/verifier, guard, GAP→ask | готово |
| Verification baseline/candidate, release/accept/archive, evidence | готово |
| Метрики, доска (снимок и live), OTLP/HTTP, CI-снимок | готово |
| Сервисы в change, verification по сервисам, merge order | готово |
| Мультикорневая запись worker'ом в открытый сервис | готово (v1) |

## Пилот

Change `add-multi-root-write` прошёл полный путь:

```
DRAFT → READY → IN_PROGRESS → IMPLEMENTED → VERIFIED → RELEASED → ACCEPTED → ARCHIVED
```

- 8 review-записей от четырёх независимых ревьюеров (BA/QA/SA/DEV), автор — `lead`;
- два круга `request_changes`: найдены и исправлены обход R-PROT-1 через
  in-root/ancestor сервис, сверка services брифа с change, отсутствие symlink-теста;
- verification: baseline и candidate 420/420, selftest 61/61;
- release: `local`, `repo=06abde2...`; приёмка: владелец (`owner`);
- evidence: `evidence/change-add-multi-root-write/manifest.json` (sha256);
- дельта синхронизирована в `openspec/specs/multi-root-write/spec.md`;
- change перенесён в `openspec/changes/archive/2026-11-16-add-multi-root-write/`.

## Дополнительно закрыто после пилота

- `add-service-commit` — tree-snapshot и commit writable-сервисов, evidence
  `task_commits`; ACCEPTED → ARCHIVED, дельта в `openspec/specs/multi-root-commit/`;
- `add-session-adapter` — контекст задачи и read-only `harness_task` в DSH;
  плагин установлен в профиль `desktop`; ACCEPTED → ARCHIVED, дельта в
  `openspec/specs/session-adapter/`;
- пять ранних change (`add-change-layer`, `add-process-roles`,
  `add-acceptance-gates`, `add-observability-board`, `add-multi-service`)
  закрыты как исторические (`CANCELLED`), без фиктивных review.

## Deployment v1 (17.11.2026)

- GitHub: <https://github.com/Grog90/harness-process> (private), workflow `ci`;
- CI: unittest + `harness ci` + `rule verify` + cosign keyless sign/verify,
  evidence artifact;
- DSH: `harness session attach` + автодиспетч на `agent/created`; привязка
  одноразовая, чужой scope — конфликт, роль attach — только оператор;
- `add-adapter-attach` — ACCEPTED → ARCHIVED, дельта в
  `openspec/specs/session-attach/`;
- `add-adapter-autosubmit` — opt-in auto-submit, ACCEPTED → ARCHIVED, дельта в
  `openspec/specs/session-autosubmit/`; evidence в `evidence/change-add-adapter-attach/`;
- runbook: [`DEPLOY.md`](DEPLOY.md).
- Ограничение: branch protection на free-private GitHub недоступен (403);
  зелёный `ci` обязателен по runbook, не технически.

## Проверки на момент закрытия

```
unittest: 508 тестов — OK (9 skipped)
selftest: 61/61
demo:     50/50
coverage: 47 правил: 44 наших, 3 delegated, 94%
rule verify: current
```

## Остаточные риски v1

- мультикорневой tree-snapshot/commit сервисов закрыт в `add-service-commit`;
  push/PR лейна и откат частичного коммита закрыты в `add-lane-push-rollback`;
  push/PR сервисных репозиториев не сделаны;
- адаптер DSH: автодиспетч и opt-in auto-submit работают; verdict/accept
  не автоматизированы, один attach на корень;
- shell-эвристика не ловит `python -c open(...,'w')`; TOCTOU между realpath и записью;
- merge order записывается и сверяется, но не исполняется как очередь; e2e
  запускается на копиях сервисов, реальные БД/Docker не поднимаются;
- `can_read`/`may_*` исполняются (`add-capability-enforcement`), но доверенная
  идентичность роли остаётся допущением;
- evidence подписан cosign и вывозится `evidence export` в доверенный каталог;
  облачного API/шифрования нет (Out of Scope);
- панель harness установлена в DSH GUI (composer.dock); HTML-доска остаётся для headless; OTel — метрики и spans переходов по команде;
- headless в CI требует `DEEPSEEK_API_KEY` в окружении job.

## Что это значит

Код процесса закрыт как v1. Дальше — либо выбранные технические долги из
[`BACKLOG.md`](BACKLOG.md), либо эксплуатация: один реальный продуктовый change с
настоящими людьми в ролях. Без второго процесс остаётся проверенным на себе, но не на
команде.
