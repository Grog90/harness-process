# Предложение: закрыть операционные долги

## Problem

После public-репозитория остались три долга: панель не получает live-данные в
Electron, публичному репо не хватает security/license/secret-scanning, а доверие
финализатора проверяется только внутри harness и CI, без независимой команды.

## What Changes

- панель: host-route `/harness-panel/metrics.json` через `ctx.webServer.register`,
  клиент делает same-origin fetch; `HARNESS_BOARD_URL` — адрес доски;
- `SECURITY.md`, `LICENSE` (all rights reserved), `docs/FINALIZER.md`;
- secret scanning и push protection включены в GitHub;
- `harness attest --ref` — независимая проверка подписи коммита без доверия к
  состоянию harness;
- README-раздел «Безопасность и лицензия».

## Impact

- Класс риска: **L1** (панель/доки/read-only проверка; запись в GitHub — настройка).
- Панель больше не зависит от CORS/mixed content: один origin.
- `attest` проверяет только подпись; tamper-proof finalizer остаётся внешней
  инфраструктурной работой, границы описаны в `docs/FINALIZER.md`.

## Out of Scope

- Полный tamper-proof finalizer (отдельный контейнер/HSM) — описан как остаток.
- Выбор открытой лицензии: сейчас all rights reserved, решение владельца.
