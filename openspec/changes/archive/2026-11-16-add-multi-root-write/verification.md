# Проверка изменения add-multi-root-write

- spec_ref: `06abde2d6f4ef817713e0948f72efad9a6d10728`
- редакция: 1
- окружение: local
- runner: unittest
- сервисы: repo@06abde2d
- итог: OK

| сервис | режим | результат | тестов | command |
|---|---|---|---|---|
| repo | baseline | pass | 420 | `"C:\Users\ownag\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe" -m unittest discover -s tests -q` |
| repo | candidate | pass | 420 | `"C:\Users\ownag\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe" -m unittest discover -s tests -q` |

Новые падения: нет
Исправлено: нет
