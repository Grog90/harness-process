# Проверка изменения add-signed-commits

- spec_ref: `725446b665cd2bf0c4120c1f8525ff7ec710ad31`
- редакция: 1
- окружение: local
- runner: unittest
- сервисы: repo@725446b6
- итог: OK

| сервис | режим | результат | тестов | command |
|---|---|---|---|---|
| repo | baseline | pass | 508 | `"C:\Users\ownag\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe" -m unittest discover -s tests -q` |
| repo | candidate | pass | 508 | `"C:\Users\ownag\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe" -m unittest discover -s tests -q` |

Новые падения: нет
Исправлено: нет
