# Проверка изменения close-operational-debt

- spec_ref: `4897aa9117e4acf5ac954506211cc851030dd407`
- редакция: 1
- окружение: local
- runner: unittest
- сервисы: repo@4897aa91
- итог: OK

| сервис | режим | результат | тестов | command |
|---|---|---|---|---|
| repo | baseline | pass | 525 | `"C:\Users\ownag\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe" -m unittest discover -s tests -q` |
| repo | candidate | pass | 525 | `"C:\Users\ownag\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe" -m unittest discover -s tests -q` |

Новые падения: нет
Исправлено: нет
