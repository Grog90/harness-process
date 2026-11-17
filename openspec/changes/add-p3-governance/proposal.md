# Предложение: P3 — PII, SLO, legal

## Problem

P3 закрывает доверие к данным и качеству: PII может попасть в логи/брифы, SLO не
измеряется, legal-инвентарь отсутствует. Evals уже реализованы (`harness evals`),
их дублировать не нужно.

## What Changes

- `harness/pii.py` + `harness pii scan|redact`: email, телефон, карта, IBAN, JWT,
  приватный ключ → `[REDACTED:<класс>]`;
- `harness/slo.py` + `harness slo [--strict]`: acceptance/rework/verification/open
  findings с порогами; пустая выборка — `n/a`, не успех;
- `docs/LEGAL.md`: инвентарь данных, хранение/вывоз, PII, лицензии, границы;
- `docs/SLO.md`: пороги и evals как отдельный контроль.

## Impact

- Класс риска: **L1** (read-only команды и документация).
- Автоматической редакции при записи нет: PII надо вычищать до коммита/экспорта.
- `slo --strict` — exit 2 только при реальном нарушении.

## Out of Scope

- DPA/GDPR-шаблоны, retention-политика с автоудалением.
- Классификация чувствительности по полям.
- Изменение evals: существующий прогон используется как есть.
