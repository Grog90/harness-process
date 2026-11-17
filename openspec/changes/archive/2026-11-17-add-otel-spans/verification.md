# Проверка изменения add-otel-spans

- spec_ref: `94831b8b56f4549c1cc75bcfae24ccd0152ff35e`
- редакция: 1
- окружение: local
- runner: unittest
- сервисы: repo@94831b8b
- итог: OK

| сервис | режим | результат | тестов | command |
|---|---|---|---|---|
| repo | baseline | pass | 470 | `"C:\Users\ownag\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe" -m unittest discover -s tests -q` |
| repo | candidate | pass | 470 | `"C:\Users\ownag\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe" -m unittest discover -s tests -q` |

Новые падения: нет
Исправлено: нет
