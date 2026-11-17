# Проверка изменения add-evidence-export

- spec_ref: `0e9e3d6c464a0d1f68c55f8eda9152bb5a62cae0`
- редакция: 1
- окружение: local
- runner: unittest
- сервисы: repo@6a855722
- итог: OK

| сервис | режим | результат | тестов | command |
|---|---|---|---|---|
| repo | baseline | pass | 464 | `"C:\Users\ownag\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe" -m unittest discover -s tests -q` |
| repo | candidate | pass | 464 | `"C:\Users\ownag\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe" -m unittest discover -s tests -q` |

Новые падения: нет
Исправлено: нет
