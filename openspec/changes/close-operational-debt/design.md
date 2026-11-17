# Design: закрытие операционных долгов

## Decisions

**1. Панель — host-route, не CORS.**
Electron-страница живёт на своём origin; прямой fetch к localhost режется. Route
`/harness-panel/metrics.json` на том же origin проксирует доску; клиент не знает
про порт. `HARNESS_BOARD_URL` — единственная настройка.

**2. Public-гигиена — файлы + настройки GitHub.**
`SECURITY.md`, `LICENSE` (all rights reserved), secret scanning/push protection
включены через API. Открытая лицензия — отдельное решение владельца.

**3. Attest — отдельная команда.**
`harness attest` запускает `git verify-commit` с allowed_signers и не читает
SQLite/состояние harness; это проверка артефакта, а не доверие процессу.

**4. Tamper-proof — честная граница.**
Текущая гарантия tamper-evident; tamper-proof требует внешнего процесса/HSM и
описан в `docs/FINALIZER.md`, а не имитируется.

## Risks

- Route доступен только после auth DSH; отдельной авторизации не добавляет.
- Board всё ещё должен быть запущен; иначе offline.
- Attest проверяет подпись, но не смысл изменения и не review.
- `LICENSE` all rights reserved: публичный просмотр не равен праву использования.
- HMR может оставить старый экземпляр панели до перезапуска DSH.
