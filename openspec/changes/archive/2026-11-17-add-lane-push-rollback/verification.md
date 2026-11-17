# Проверка изменения add-lane-push-rollback

- spec_ref: `98b67c5e66cd9b721cdcae47f2a421ae63c12c58`
- редакция: 1
- окружение: local
- runner: unittest
- сервисы: repo@98b67c5e
- итог: OK

| сервис | режим | результат | тестов | command |
|---|---|---|---|---|
| repo | baseline | pass | 446 | `"C:\Users\ownag\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe" -m unittest discover -s tests -q` |
| repo | candidate | pass | 446 | `"C:\Users\ownag\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe" -m unittest discover -s tests -q` |

Новые падения: нет
Исправлено: нет
