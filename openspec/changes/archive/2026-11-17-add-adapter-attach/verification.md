# Проверка изменения add-adapter-attach

- spec_ref: `85be196a6e10bbd8c082851cf48d44eddd9999a8`
- редакция: 1
- окружение: local
- runner: unittest
- сервисы: repo@85be196a
- итог: OK

| сервис | режим | результат | тестов | command |
|---|---|---|---|---|
| repo | baseline | pass | 434 | `"C:\Users\ownag\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe" -m unittest discover -s tests -q` |
| repo | candidate | pass | 434 | `"C:\Users\ownag\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe" -m unittest discover -s tests -q` |

Новые падения: нет
Исправлено: нет
