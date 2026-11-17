# Проверка изменения add-forge-status

- spec_ref: `d21edf21f4a1dfe8592531ca32bb6e77724e29d0`
- редакция: 1
- окружение: local
- runner: unittest
- сервисы: repo@d21edf21
- итог: OK

| сервис | режим | результат | тестов | command |
|---|---|---|---|---|
| repo | baseline | pass | 454 | `"C:\Users\ownag\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe" -m unittest discover -s tests -q` |
| repo | candidate | pass | 454 | `"C:\Users\ownag\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe" -m unittest discover -s tests -q` |

Новые падения: нет
Исправлено: нет
