# Десктоп для агента: agent-desktop

Проверено 2026-11-01 на Windows x64 (agent-desktop 1.0.1). Здесь: зачем десктоп,
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
| бинарник | `.tools/agent-desktop/pkg/v1.0.1/agent-desktop.exe` | 1.0.1 |
| бинарник (сломан) | `.tools/agent-desktop/pkg/v1.0.2/agent-desktop.exe` | 1.0.2 — оставлен для разбора |
| обёртка | `tools/desktop_mcp.py` | 1.0.0 |
| запись MCP | `cordis.patch.yml` профиля DSH | — |

### Установка закреплённой версии

Обёртка **ничего не скачивает** сама. Порядок ручной:

```powershell
# 1. скачать архив и контрольные суммы того же релиза
curl -fL -o ad.tgz https://github.com/vercel-labs/agent-desktop/releases/download/v1.0.1/agent-desktop-v1.0.1-x86_64-pc-windows-msvc.tar.gz
curl -fL -o checksums.txt https://github.com/vercel-labs/agent-desktop/releases/download/v1.0.1/checksums.txt

# 2. сверить sha256 с checksums.txt — глазами, до распаковки
Get-FileHash ad.tgz -Algorithm SHA256

# 3. распаковать в .tools/agent-desktop/pkg/v1.0.1/ и проверить запуск
tar -xzf ad.tgz -C .tools/agent-desktop/pkg/v1.0.1
.\.tools\agent-desktop\pkg\v1.0.1\agent-desktop.exe status
```

Ожидаемый `status`: `"accessibility": {"state": "granted"}`. Путь можно
переопределить переменной `DESKTOP_BIN`.

### Почему закреплена 1.0.1, а не 1.0.2

На этой машине 1.0.2 не работает: `launch` отвечает
`INTERNAL: lock directory is not trustworthy: Отказано в доступе. (os error 5)`,
`list-apps` — `INTERNAL: a window-owning process is absent from the process snapshot`,
`snapshot` — `os error 5`, а `app_name` у всех окон пустой. При этом UI Automation
доступен: те же окна читаются из PowerShell через `System.Windows.Automation`.
Проверка 1.0.1 на том же рабочем столе проходит полностью. Поэтому обёртка ищет
только `v1.0.1` явным путём и **не** перебирает каталоги версий.

## 3. Инструменты

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

## 4. Правила, которые обёртка держит сама

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

## 5. Границы и известные ляпы

- **Права.** Работает только в интерактивном сеансе пользователя. Окна с
  повышением прав (UAC), secure desktop и заблокированный экран не видны:
  `PERM_DENIED` или пустое дерево. Чужой сеанс не поддерживается.
- **Подпись.** Бинарник не подписан — SmartScreen и антивирус могут ругаться;
  контрольная сумма проверяется только вручную, при установке.
- **Свёрнутые окна** не оживают: сначала `restore`, потом снимок.
- **`snapshot --app X` при незапущенном приложении** отвечает
  «running but no matching window» — сообщение врёт, процесс не запущен.
  Проверяй `list_apps`.
- **`SNAPSHOT_NOT_FOUND`.** Снимки и ref живут в состоянии бинарника
  (`~/.agent-desktop` по умолчанию, переопределяется `AGENT_DESKTOP_HOME`).
  Если снимок сделан при одном состоянии, а действие выполняется при другом —
  ref не найдётся. Обёртка всегда работает с одним состоянием.
- **`app_name` в `list_windows`** у части окон пустой; фильтр `--app` идёт по
  имени процесса, поэтому бери имя из `list_apps`.

## 6. Регистрация в профиле

Одна запись в `cordis.patch.yml` профиля DSH, в существующем списке MCP-серверов:

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
        toolCallTimeoutMs: 90000
```

После правки — перезапуск приложения: инструменты читаются при старте профиля.

## 7. Проверка без перезапуска

```powershell
'{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | python tools/desktop_mcp.py
```

Ответ — строка JSON с восемью инструментами. Полный живой прогон (нужен
открытый Блокнот):

```powershell
'{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"snapshot","arguments":{"app":"Notepad.exe","max_nodes":50}}}' | python tools/desktop_mcp.py
```

- **Состояние на 2026-11-05.** На этой машине бинарник начал отвечать
  INTERNAL: lock directory is not trustworthy: Отказано в доступе. (os error 5)
  и INTERNAL: a window-owning process is absent from the process snapshot —
  одинаково у 1.0.1 и 1.0.2, при доступном UI Automation из PowerShell и
  пересозданном state_root. Обёртка такие ответы отдаёт как есть, с кодом и
  etry; это дефект бинарника, а не обёртки. Разбор — отдельной задачей.
