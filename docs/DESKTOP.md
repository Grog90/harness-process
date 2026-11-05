# Десктоп для агента: agent-desktop

Проверено 2026-11-05 на Windows x64 (agent-desktop 1.0.1). Здесь: зачем десктоп,
почему именно эта программа, как она подключена, где её границы и что делать,
когда она отказывает.

## 1. Зачем

`browser` закрывает веб. Всё остальное — Блокнот, Калькулятор, проводник, своё
приложение — браузеру недоступно. `agent-desktop` читает **дерево доступности**
окна (UI Automation на Windows) и дёргает элементы по ссылкам `ref`, а не по
пикселям: попадание не зависит от масштаба, темы и шрифтов.

## 2. Что установлено

| Что | Где | Версия |
|---|---|---|
| бинарник | `C:\Tools\agent-desktop\v1.0.1\agent-desktop.exe` | 1.0.1 |
| копия (не использовать) | `.tools/agent-desktop/pkg/v1.0.1`, `.../v1.0.2` | 1.0.1, 1.0.2 |
| обёртка | `tools/desktop_mcp.py` | 1.0.0 |
| запись MCP | `cordis.patch.yml` профиля DSH | — |

Рабочая копия лежит **вне проекта**, на коротком пути без кириллицы. Это не
косметика: см. § 3.

### Установка закреплённой версии

Обёртка **ничего не скачивает** сама. Порядок ручной:

```powershell
# 1. скачать архив и контрольные суммы того же релиза
curl -fL -o ad.tgz https://github.com/vercel-labs/agent-desktop/releases/download/v1.0.1/agent-desktop-v1.0.1-x86_64-pc-windows-msvc.tar.gz
curl -fL -o checksums.txt https://github.com/vercel-labs/agent-desktop/releases/download/v1.0.1/checksums.txt

# 2. сверить sha256 с checksums.txt — глазами, до распаковки
Get-FileHash ad.tgz -Algorithm SHA256

# 3. распаковать на короткий путь и проверить запуск
New-Item -ItemType Directory -Force -Path C:\Tools\agent-desktop\v1.0.1 | Out-Null
tar -xzf ad.tgz -C C:\Tools\agent-desktop\v1.0.1
C:\Tools\agent-desktop\v1.0.1\agent-desktop.exe status
```

Ожидаемый `status`: `"accessibility": {"state": "granted"}`. Путь к бинарнику
можно переопределить переменной `DESKTOP_BIN` — обёртка смотрит её первой.

## 3. Правило пути (дорого выученное)

Бинарник **нельзя** держать в папке проекта, если путь длинный и с кириллицей.
Проверено 2026-11-05 одним и тем же файлом (sha256 `e2551cbd…`) в одну секунду:

| Путь к exe | `snapshot` |
|---|---|
| `C:\Tools\agent-desktop\v1.0.1\agent-desktop.exe` | ok, 195 элементов |
| `…\OneDrive\Документы\deepseek-harness\default-workspace\probe.exe` | `lock directory is not trustworthy: Отказано в доступе. (os error 5)` либо `WINDOW_NOT_FOUND` |

Разложено по частям: кириллица при коротком пути — ок; латиница при длинном
пути — ок; длинный путь **и** кириллица — падает. Сообщение про «каталог
блокировки» — ложный след: `io_untrusted()` в
`crates/windows/src/system/interaction_lease/directory.rs` склеивает любую
`io::Error` в один текст.

**Поэтому:** агентские бинарники ставить на короткий ASCII-путь
(`C:\Tools\…`), а `DESKTOP_BIN` держать в профиле.

### Почему закреплена 1.0.1, а не 1.0.2

Отдельно от пути: 1.0.2 на этой машине отвечала `INTERNAL: lock directory is not
trustworthy`, `INTERNAL: a window-owning process is absent from the process
snapshot` и не отдавала имена приложений ни для одного окна. 1.0.1 в тех же
условиях работает. Разбирать 1.0.2 отдельно; понижение до 1.0.1 — сознательное.

## 4. Инструменты

| Инструмент | Что делает |
|---|---|
| `desktop_status` | какая версия и путь выбраны, какие поверхности поддерживаются |
| `list_apps` | запущенные приложения с окнами |
| `list_windows` | окна: id, приложение, заголовок, свёрнуто |
| `snapshot` | дерево окна построчно: `ref \| роль \| имя \| действия`, до `max_nodes` |
| `find` | поиск по роли и имени без полного снимка |
| `act` | `click`, `set_value`, `press`, `toggle`, `check`, `uncheck`, `scroll` по ref |
| `wait_for` | пауза, текст, окно, меню |
| `screenshot` | PNG окна в `.desktop/`, картинка в ответ до 3 МиБ |

Цикл: `snapshot` или `find` → `act` по `ref` → снова `snapshot`.

## 5. Правила, которые обёртка держит сама

- **Физический ввод запрещён.** `--headed` не передаётся никогда. `type`,
  `right-click`, `double-click`, `hover`, `drag`, `focus` и мышиные команды
  отклоняются с `ACTION_NOT_ALLOWED_HEADED`: для текста есть `set_value`.
- **Действие не повторяется.** Обёртка делает ровно один вызов и отдаёт
  `delivery` и `retry` из ответа бинарника. `retry=unsafe` означает «могло
  сработать» — повторять нельзя, надо перечитать окно.
- **Сеть не трогается.** В `tools/desktop_mcp.py` нет `urllib`, `requests`,
  `httpx` и `socket`; установка и обновление — только вручную.
- **Данные не логируются.** Значения полей, буфер обмена и текст окон не
  пишутся в файлы; в stderr уходит только диагностика запуска.

## 6. Границы и известные ляпы

- **Права.** Работает только в интерактивном сеансе пользователя. Окна с
  повышением прав (UAC), secure desktop и заблокированный экран не видны:
  `PERM_DENIED` или пустое дерево. Чужой сеанс не поддерживается.
- **Подпись.** Бинарник не подписан — SmartScreen и антивирус могут ругаться;
  контрольная сумма проверяется только вручную, при установке.
- **Свёрнутые окна** не оживают: сначала `restore`, потом снимок.
- **`list_apps` и UWP.** У упакованных приложений (Калькулятор) `list_apps`
  показывает `ApplicationFrameHost.exe`, а окно принадлежит `CalculatorApp.exe`.
  Бери имя из `list_windows`, а не из `list_apps`.
- **`snapshot --app X` при незапущенном приложении** отвечает
  «running but no matching window» — сообщение врёт, процесс не запущен.
- **`SNAPSHOT_NOT_FOUND`.** Снимки и ref живут в состоянии бинарника
  (`~/.agent-desktop` по умолчанию, переопределяется `AGENT_DESKTOP_HOME`).
  Если снимок сделан при одном состоянии, а действие выполняется при другом —
  ref не найдётся. Обёртка всегда работает с одним состоянием.

## 7. Регистрация в профиле

Одна запись в `cordis.patch.yml` профиля DSH, в общем списке MCP-серверов:

```yaml
    - id: mcp-desktop
      name: '@deepseek-ai/dsh-mcp-client'
      config:
        serverName: desktop
        transport: stdio
        command: 'C:\Users\ownag\AppData\Local\Programs\Python\Python313\python.exe'
        args:
          - 'C:\Users\ownag\OneDrive\Документы\deepseek-harness\default-workspace\tools\desktop_mcp.py'
        cwd: 'C:\Users\ownag\OneDrive\Документы\deepseek-harness\default-workspace'
        env:
          DESKTOP_BIN: 'C:\Tools\agent-desktop\v1.0.1\agent-desktop.exe'
        toolCallTimeoutMs: 90000
        failOnStartupError: false
```

`DESKTOP_BIN` обязателен: без него обёртка ищет бинарник в `.tools/…` внутри
проекта, то есть на том самом длинном пути с кириллицей. После правки —
перезапуск приложения: инструменты читаются при старте профиля.

## 8. Проверка

Без перезапуска:

```powershell
'{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | python tools/desktop_mcp.py
```

Ответ — строка JSON с восемью инструментами.

Живой прогон (2026-11-05, Калькулятор): `snapshot` отдал 56 элементов с русскими
именами, `act click` по кнопке «Семь» вернул `delivered_unverified`, повторный
`snapshot` показал дисплей `7` вместо `0` — нажатие подтверждено перечитыванием.
