# Проверка изменения add-finalizer-signing

- spec_ref: `1c83d9b9cfcae1bb8cacee954bdf3f60af623556`
- редакция: 1
- окружение: local
- runner: unittest
- сервисы: repo@1c83d9b9
- итог: OK

| сервис | режим | результат | тестов | command |
|---|---|---|---|---|
| repo | baseline | pass | 517 | `"C:\Users\ownag\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe" -m unittest discover -s tests -q` |
| repo | candidate | pass | 517 | `"C:\Users\ownag\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe" -m unittest discover -s tests -q` |

Новые падения: нет
Исправлено: нет
