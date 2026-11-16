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

## Проверки на момент закрытия

```
unittest: 423 теста — OK (9 skipped)
selftest: 61/61
demo:     50/50
coverage: 47 правил: 44 наших, 3 delegated, 94%
rule verify: current
```

## Остаточные риски v1

- мультикорневой tree-snapshot/commit сервисов закрыт в `add-service-commit`;
  push/PR и откат частичного коммита не сделаны;
- shell-эвристика не ловит `python -c open(...,'w')`; TOCTOU между realpath и записью;
- merge order записывается, но не исполняется; e2e-среды нет;
- `can_read`/`may_*` остаются декларациями, доверенная идентичность — допущение;
- evidence не подписан и не вывозится во внешнее хранилище;
- live-доска — локальный сервер, не панель DSH GUI; OTel — только метрики;
- headless в CI требует `DEEPSEEK_API_KEY` в окружении job.

## Что это значит

Код процесса закрыт как v1. Дальше — либо выбранные технические долги из
[`BACKLOG.md`](BACKLOG.md), либо эксплуатация: один реальный продуктовый change с
настоящими людьми в ролях. Без второго процесс остаётся проверенным на себе, но не на
команде.
