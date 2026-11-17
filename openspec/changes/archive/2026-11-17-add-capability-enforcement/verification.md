# Проверка изменения add-capability-enforcement

- spec_ref: `81aeb67286a067d95d90bb4636d24825ea6fad74`
- редакция: 1
- окружение: local
- runner: unittest
- сервисы: repo@81aeb672
- итог: OK

| сервис | режим | результат | тестов | command |
|---|---|---|---|---|
| repo | baseline | pass | 440 | `"C:\Users\ownag\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe" -m unittest discover -s tests -q` |
| repo | candidate | pass | 440 | `"C:\Users\ownag\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe" -m unittest discover -s tests -q` |

Новые падения: нет
Исправлено: нет
