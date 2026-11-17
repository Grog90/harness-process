# Проверка изменения add-multi-service-e2e

- spec_ref: `a96ecf97a937c14c93223dab8777b80b1f26d0ac`
- редакция: 1
- окружение: local
- runner: unittest
- сервисы: repo@a96ecf97
- итог: OK

| сервис | режим | результат | тестов | command |
|---|---|---|---|---|
| repo | baseline | pass | 486 | `"C:\Users\ownag\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe" -m unittest discover -s tests -q` |
| repo | candidate | pass | 486 | `"C:\Users\ownag\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe" -m unittest discover -s tests -q` |

Новые падения: нет
Исправлено: нет
