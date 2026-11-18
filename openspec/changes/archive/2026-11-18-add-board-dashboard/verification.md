# Проверка изменения add-board-dashboard

- spec_ref: `761f5a7c79e5532fc073b5e711ba4fc5521bd979`
- редакция: 1
- окружение: local
- runner: unittest
- сервисы: repo@761f5a7c
- итог: OK

| сервис | режим | результат | тестов | command |
|---|---|---|---|---|
| repo | baseline | pass | 527 | `"C:\Users\ownag\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe" -m unittest discover -s tests -q` |
| repo | candidate | pass | 527 | `"C:\Users\ownag\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe" -m unittest discover -s tests -q` |

Новые падения: нет
Исправлено: нет
