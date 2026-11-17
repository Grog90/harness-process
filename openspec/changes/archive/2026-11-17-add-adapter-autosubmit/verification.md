# Проверка изменения add-adapter-autosubmit

- spec_ref: `fe617c12a653c1df0f93f54e02981a79dafe48ef`
- редакция: 1
- окружение: local
- runner: unittest
- сервисы: repo@fe617c12
- итог: OK

| сервис | режим | результат | тестов | command |
|---|---|---|---|---|
| repo | baseline | pass | 435 | `"C:\Users\ownag\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe" -m unittest discover -s tests -q` |
| repo | candidate | pass | 435 | `"C:\Users\ownag\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe" -m unittest discover -s tests -q` |

Новые падения: нет
Исправлено: нет
