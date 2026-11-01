# Инвентарь контура обхода блокировок

Снято 29.10.2026 с живого роутера. Цифры меняются — перед выводами перечитывай
`GET /api/routing/dns-routes`, `GET /api/singbox/router/status`,
`GET /api/routing/tunnels`.

## 1. Железо и софт

| Что | Значение |
|---|---|
| Роутер | Keenetic Giga (KN-1012), MT7981, arm64, 486 МБ RAM |
| Прошивка | 5.1.8 (`5.01.C.8.0-2`), sandbox stable, регион EA |
| VPN-компоненты | WireGuard, OpenVPN, L2TP (штатные) |
| AWG Manager | **2.19.22**, `http://192.168.1.1:2222` |
| nfqws2 | пакет **`nfqws2-keenetic 1.3.2`**, бинарь `/opt/usr/bin/nfqws2`, v1.0.6 (lua_compat_ver 6) |
| Панель nfqws2 | **`http://192.168.1.1:90`** (`nfqws-keenetic-web 3.0.24`, lighttpd + php8) |
| sing-box | **1.15.0-alpha.13-awgm.35** — сборка самого AWGM, качает сам |
| SSH | порт 22 — CLI Keenetic, порт 222 — Entware dropbear |
| WAN | `ppp0` (PPPoE), IP из пула `77.83.3.x` |

## 2. Хранилище (важно)

| Что | Где |
|---|---|
| Entware | внутренняя UBI-флеш, `/opt`, 98 МБ всего |
| USB-накопитель | ext4, 7.3 ГБ, метка `entware`, монтируется в `/tmp/mnt/<uuid>` |
| `awg-manager` | **на USB**: `/tmp/mnt/<uuid>/keenetic/bin/awg-manager`, в `/opt/bin` симлинк |
| `sing-box` | **на USB**: `/tmp/mnt/<uuid>/keenetic/singbox/sing-box` |
| Аварийные копии | `/opt/backup-bins/{awg-manager,sing-box}.gz` — если USB не поднялся |
| Boot-скрипт | `/opt/etc/init.d/S93usb-bins` — ждёт USB до 30 с, делает `remount,exec`, ставит симлинки, иначе разворачивает gz |

Ловушки:

- Keenetic монтирует съёмные носители **с `noexec`** — без `mount -o remount,exec`
  бинарник с USB не запускается.
- Если USB не вставлен/не поднялся, `awg-manager` — битый симлинк, панель и MCP
  мертвы. Спасает `S93usb-bins` (разворачивает gz на внутреннюю память).
- После физического переподключения флешки Keenetic видит её снова; при
  «не появился `/dev/sda`» — попросить владельца переподключить.

## 3. VPS

| Что | Значение |
|---|---|
| Адрес | **91.103.253.94** (Ubuntu 24.04, Амстердам, NL, AS200740 First Server) |
| Hysteria 2 сервер | 2.13, `/usr/local/bin/hysteria`, unit смотрит на **`/etc/hysteria/config.yaml`** |
| Сертификат | **Let's Encrypt** через встроенный ACME (DNS-01, провайдер DuckDNS), домен **`knt-gw-7731.duckdns.org`**; кэш в `/var/lib/hysteria/acme`, продление автоматическое (первые ~90 дней до 28.01.2027) |
| Порт | **UDP 443**, `listen :443`, **`obfs: salamander`**, masquerade → bing.com (с obfs сервер перестаёт отвечать как HTTP/3 — это ожидаемо) |
| Особенность | сервис бежит под пользователем **`hysteria`** — `key.pem` должен быть ему читаем (иначе `permission denied`) |
| Ресурсы | 1 ядро, 961 МБ RAM, 8,8 ГБ диска (занято ~5 ГБ) — Grafana сюда не влезает |

Доступ: ключ `~/.ssh/id_ed25519_vps`, пользователь `deploy` (NOPASSWD sudo).

**Мёртвые серверы:** `177.177.230.47` (выведен 29.10.2026, не отвечает — на нём
жили Prometheus/Grafana, они умерли вместе с ним) и `185.200.190.149` (заблокирован
по IP, там был AmneziaWG + 3x-ui + WARP).

## 4. Туннели

| id | Имя | Интерфейс | Локальный порт | Состояние | Куда |
|---|---|---|---|---|---|
| `hy2-knt-gw-7731.duckdns.org-443` | hysteria2 + **salamander**, SNI = домен, **без `insecure`** | **`Proxy0`** | 1080 | running, **рабочий** | knt-gw-7731.duckdns.org:443/udp |

Старые `hy2-91.103.253.94-443` (Proxy1) и `hy2-177.177.230.47-443` удалены 30.10.2026.

Клиент привязан к **домену**, а не к IP: при смене IP сервера достаточно поправить
A-запись в DuckDNS (`https://www.duckdns.org/update?domains=knt-gw-7731&token=…&ip=НОВЫЙ_IP`),
на роутере ничего не меняется. Туннель на IP (`sni=de-vps.local`, `insecure: true`)
с ACME **не работает**: сертификат выбирается по SNI, и сервер отвечает
`CRYPTO_ERROR 0x150 (tls: internal error)`.

**Ловушка 30.10.2026 (стоила часа):** после удаления и пересоздания sing-box
туннеля AWGM пишет в лог «route block rewritten in place: `[Proxy0]`», но в NDMS
маршруты **не устанавливаются** — весь LAN-трафик уходит `Direct (ppp0)`,
Telegram/Instagram висят в `SYN_SENT`, «интернета нет». Лечится
`/opt/etc/init.d/S99awg-manager restart` (~30 с, блоки пересобираются при старте).

Проверять надо **не панелью и не `curl -x 127.0.0.1:1080`**: пробы blackbox и
локальный прокси работают в обход NDMS и остаются зелёными при полностью
неработающей маршрутизации. Честный тест — `GET /api/connections?limit=500`:
в поле `tunnelName` должно быть имя туннеля (`hy2-…`), а не `Direct (ppp0)`.

`awg11` / `opkgtun11` больше нет: AWG-туннелей на роутере **ноль**, всё на sing-box.

**Важно:** у каждого sing-box туннеля свой локальный порт. Пробник `blackbox` в
`/opt/var/monitoring/blackbox.yml` ходит в **активный**, сейчас это
`proxy_url: http://127.0.0.1:1080`. При смене туннеля порт надо менять руками,
**и перезапускать blackbox**: `S99monitoring restart` сам не подхватывает конфиг —
проверено 30.10.2026, помог только `stop` → `start` (панель показывала `code=0`
на живом туннеле до перезапуска).

Туннель Hysteria создан **из share-ссылки** через `POST /api/singbox/tunnels`
(`{"links":"hy2://…"}`); AWGM сам создал `Proxy0` и kernel-интерфейс `t2s0`.

## 5. Списки маршрутизации (AWGM, backend ndms)

| id | Имя | Цель | Вкл |
|---|---|---|---|
| `list_2` | Заблокировано в РФ (itdoginfo) | `system:Proxy1` | да |
| `list_3` | Недоступно из РФ (hoaxisr) | `system:Proxy1` | да |
| `list_4` | **Discord** | **`wan:ppp0`** (через nfqws2) | да |
| `list_5` | Google | `awg11` | нет |
| `list_6` | Instagram | `system:Proxy1` | да |
| `list_7` | NVIDIA | `system:Proxy1` | да |
| `list_8` | Spotify | `system:Proxy1` | да |
| `list_9` | Telegram | `system:Proxy1` | да |
| `list_10` | Torrents | `system:Proxy1` | да |
| `list_11` | WhatsApp | `system:Proxy1` | да |
| `list_12` | **YouTube → nfqws2** | **`wan:ppp0`** | да |
| `list_13` | GitHub | `system:Proxy1` | да |
| `list_14` | Все AI сервисы | `awg11` | нет |
| `list_15` | Gemini | `awg11` | нет |
| `list_16` | pron | `system:Proxy1` | да |
| `list_17` | AI без Google (VPN) | `system:Proxy1` | да |

Смена цели: `POST /api/dns-routes/update?id=list_N` с
`{"routes":[{"tunnelId":"system:Proxy1"}]}` (или `wan:ppp0`). Тело с `routes`
заменяет цель, домены при этом сохраняются.

## 6. nfqws2 — рабочий конфиг ALT3

Файлы:

| Что | Путь |
|---|---|
| Конфиг | `/opt/etc/nfqws2/nfqws2.conf` |
| Бэкап рабочего до ALT3 | `/opt/etc/nfqws2/nfqws2.conf.bak-alt3` |
| Init | `/opt/etc/init.d/S51nfqws2` (`start|stop|restart`) |
| Списки | `/opt/etc/nfqws2/lists/` |
| Блобы | `/opt/etc/nfqws2/blobs/` |
| Лог | `/opt/var/log/nfqws2.log` |

Списки:

| Файл | Строк | Назначение |
|---|---|---|
| `list-general.list` | 59 | Discord (23), Cloudflare, DoH-резолверы, Twitch-расширения |
| `list-google.list` | 22 | YouTube, googlevideo, ytimg, ggpht, googleapis |
| `user.list` | 273 | штатный список пакета, **не тронут** |
| `user.list.pre-alt3` | — | бэкап перед разделением |
| `exclude.list`, `ipset*.list` | — | штатные исключения |
| `auto.list` | 0 | не используется (`MODE_LIST`, не `MODE_AUTO`) |

Блобы (скопированы с ПК из `zapret-discord-youtube-1.10.4/bin`):

`tls_clienthello_www_google_com.bin` (681), `quic_initial_www_google_com.bin` (1200),
`ACTIVE_DISCORD_UDP.bin` (1200), `tls_clienthello_www_sferum_ru.bin` (661), `stun.bin` (100).

Профили (порядок = порядок сборки в `S51nfqws2`):

```
BASE   : --fastpath-workaround=auto, lua-init × 3, --blob=×(4)
CUSTOM : 1) TCP 443 + list-google      → fake(sni=www.google.com,tcp_ts=-600000)
                                         + hostfakesplit(host=www.google.com,
                                           disorder_after=-1,tcp_ts=-600000,ip_id=zero)
         2) TCP 2053,2083,2087,2096,8443 + discord.media → то же без ip_id
UDP    : UDP 19294-19344,50000-50100 + l7=discord,stun → fake(discord_udp,repeats=6)
QUIC   : UDP 443 + l7=quic → fake(quic_initial,repeats=6)
ARGS   : TCP 80,443 + MODE_LIST(list-general) → fake(sni=ya.ru,tcp_ts=-600000)
                                         + hostfakesplit(host=ya.ru,disorder_after=-1,tcp_ts=-600000)
                                         + http: fake(sferum) + http_methodeol(badsum)
```

Сборка аргументов в init-скрипте (важно при правках):

```
args = --user --qnum BASE
args += CUSTOM --new
args += UDP --new
args += QUIC --new
args += ARGS EXTRA
```

`--new` внутри `NFQWS_ARGS*` запрещён — скрипт выдаёт ошибку.

## 7. Проверенные цифры (для сравнения при регрессии)

| Замер | Значение |
|---|---|
| YouTube: страница | 200, ~965 КБ |
| YouTube: видео | `playing:true`, `854×480`, буфер 49 с, `currentTime` растёт |
| Discord web через nfqws2 | **1067×200, 0 ошибок TLS** |
| Discord web через multisplit (старая стратегия) | 253×200 + **386 SSL-ошибок** |
| Discord app | окно `#1 … - Discord`, heartbeat/ACK каждые ~14 с |
| Discord голос | средняя 69 мс, последняя 68 мс, потери 0.0 % |
| Hysteria: выход | `ipChanged: true`, **91.103.253.94**, latency ~217 мс (замер на старом сервере) |
| Hysteria: скорость | через туннель **74 Мбит/с**, напрямую 78 (замер 29.10.2026 на новом сервере) |

## 8. Где лежат доступы (не секреты, а места)

| Что | Где |
|---|---|
| SSH-пароль роутера | `~/.dsh/secrets/keenetic.env` (`KEENETIC_USER`, `KEENETIC_PASS`), вход через `plink -ssh -l admin -pw <pass> 192.168.1.1` — только CLI Keenetic, не shell |
| Ключ AWGM API | `~/.dsh/secrets/awgm.env` (`AWGM_MCP_KEY=…`), также `/opt/etc/awg-manager/mcp_keys.json` |
| SSH-ключ VPS | `~/.ssh/id_ed25519_vps` |
| Пароль Hysteria | в конфиге клиента AWGM (`GET /api/singbox/tunnels/get?tag=…`) и на VPS |

## 9. История и тупики (не повторять)

1. **Старый VPS 185.200.190.149 заблокирован по IP** — AmneziaWG и VLESS Reality
   на нём умерли. Был поднят VPS 177.177.230.47 с Hysteria 2 — **тоже мёртв с 29.10.2026**, заменён на 91.103.253.94.
2. **sing-box ставили вручную** (Entware-пакет + распаковка в tmpfs), потом AWGM
   сам скачал свою сборку 1.15.0 — ручная больше не нужна.
3. **Попытка добавить hysteria2 как outbound в слот `config/user`** — AWGM его не
   видит, правила становятся `orphan-rule`. Правильный путь: `POST /api/singbox/tunnels`
   с share-ссылкой.
4. **Режим `tproxy`** — требует привязки устройств к политике; у политики нет
   маршрута по умолчанию → устройство без интернета, правила перехвата не
   создаются. Тупик.
5. **Привязка ПК/телефона к `Policy2` (Hwave)** — та же причина, интернет умирает.
6. **Заворот `ggpht.com` в туннель** — на старом (ныне мёртвом) VPS картинки ломались.
   На рабочем `Proxy1` это наоборот единственное решение — см. §15.
7. **Первая попытка переноса ALT2** (`multisplit` + `seqovl` + `badseq`) — Discord
   получал сотни SSL-ошибок, приложение висело на `Discord Updater`.
8. **Первая попытка переноса ALT3** — взяли `tcp_ts_up` вместо `tcp_ts=-600000` и
   потеряли `disorder_after=-1`; YouTube отдавал `000`.
9. **moli (MCP browser) врёт** про картинки и кэширует скриншоты — для проверки
   только `chrome_*` (настоящий Chrome через CDP) или `tools/webshot.ps1`.
10. **Правило поведения агента**: без явного «да» ничего не менять, **не
    откатывать самому**. Файл `AGENT_RULES.md` в корне воркспейса.

## 10. Мониторинг YouTube (этап 1 — пассивный сбор, 29.10.2026)

Что установлено:

| Файл | Роль |
|---|---|
| `/opt/etc/yt-monitor.sh` | снимок `conntrack` по Google-диапазонам → CSV |
| `/opt/etc/yt-monitor-loop.sh` | бесконечный цикл: снимок, `sleep 30` |
| `/opt/etc/init.d/S97yt-monitor` | `start|stop|restart|status`, автозапуск |
| `/tmp/mnt/<uuid>/keenetic/yt-monitor/yt-YYYY-MM-DD.csv` | данные на USB, хранение 7 дней |

Формат CSV:

```
ts,mac,src,dst,proto,dport,state,bytes_out,bytes_in,pkts_out,pkts_in
2026-10-29 18:00:11,04:7c:16:17:6f:8f,192.168.1.72,74.125.205.188,tcp,443,ESTABLISHED,25788,39926,535,538
```

Разница байтов между снимками = скорость потока; поток с растущим временем и
неизменными байтами = «залипший» (медленный CDN или блокировка).

**Грабли: cron на Keenetic не годится** — busybox `crond` запускает задания через
shell пользователя, а у `root` это `/bin/ndmc` (CLI роутера). Задания
«стартуют» и молча ничего не делают. Поэтому используется init-скрипт с
`start-stop-daemon`, а не `/opt/var/spool/cron/crontabs/root`.

Следующие этапы (не реализованы): активные ping-пробы CDN, захват `tcpdump` по
кнопке с разбором ретрансмитов.

### 10.1 Этапы 2 и 3 (реализованы 29.10.2026)

| Файл | Роль |
|---|---|
| `/opt/etc/yt-probe.sh` | пинг топ-3 адресов по трафику → `probe-YYYY-MM-DD.csv` |
| `/opt/etc/yt-analyze.sh` | разбор CSV: трафик по устройствам, залипшие потоки, активные |
| `/opt/etc/yt-capture.sh [сек]` | захват `tcpdump` к Google + разбор на месте → `capture-*.txt` |

Цикл демона: снимок каждые 30 с, пинг CDN каждые 60 с.

Что показывает `yt-capture.sh`:

- пакеты, байты, **наши фейки** (TTL ≤ 64 — так помечены пакеты, сгенерированные
  nfqws2; их надо исключать из подсчёта ретрансмитов);
- ретрансмиты по `seq` (без фейков) и их доля;
- RST и потоки, где они есть (признак вмешательства DPI);
- топ потоков по ретрансмитам.

Важно: `tcpdump` нельзя запускать с `-q` — флаг убирает `Flags`/`seq` из вывода и
разбор получает ноль пакетов. Нужны `-n -tt -S -v`.

Хранение: 2 дня (`-mtime +2`), данные и отчёты на USB.

### 10.2 Диагноз лагов Shorts (29.10.2026)

Симптом: обычные видео играют ровно, Shorts «средне грузятся».

Что выяснилось:

1. YouTube отдаёт видео с **Google Global Cache внутри Ростелекома**:
   `rr2---sn-gvnuxaxjvh-3gg6.googlevideo.com` → `79.133.77.45` (AS12389).
   Такой ответ дают все резолверы, включая 8.8.8.8 и 1.1.1.1 — это не подмена DNS,
   а штатный GGC.
2. Плеер сначала пробует **QUIC (UDP/443)** до этого узла. В conntrack видно:
   клиент отправляет 12–13 пакетов, сервер отвечает 1 пакетом (68 байт) — QUIC не
   поднимается. Плеер ждёт таймаут **3–5 секунд** и только потом идёт по TCP.
3. В Resource Timing это выглядит как сегменты с `duration` 3548/5415/5480 мс и
   `transferSize = 0`.

Решение (реализовано): `S98quic-block` — `iptables -I FORWARD -p udp --dport 443
-d <сеть> -j REJECT --reject-with icmp-port-unreachable` для диапазонов
Google и `79.133.0.0/16`. QUIC отваливается мгновенно, плеер сразу берёт TCP.

Замеры (5 Shorts подряд, `python tools/yt_check.py --shorts --slides 5`):

| | до правила | после |
|---|---|---|
| слайд 1 | сегмент 5415 мс | макс 469 мс |
| слайды 3, 5 | проиграно 1.4 и 2.1 с (рывки) | по 10 с |
| вердикт | ПРОБЛЕМА | OK |

Откат: `/opt/etc/init.d/S98quic-block stop`.





## 11. Мониторинг (актуальное, 29.10.2026)

**http://91.103.253.94:8080** — панель `netmon`, без пароля. Описание:
`docs/NETMON.md` в воркспейсе.

| Что | Где |
|---|---|
| сервис | `/home/deploy/netmon/netmon.py`, unit `netmon.service`, `MemoryMax=200M` |
| туннель роутер → VPS | `tunnel@91.103.253.94`, проброс `127.0.0.1:9115` (blackbox) и `127.0.0.1:2222` (API роутера) |
| на роутере | `/opt/var/monitoring/` (blackbox, `tunnel_loop.sh`), init `/opt/etc/init.d/S99monitoring` |
| ключ туннеля | dropbear-ключ `/opt/var/monitoring/tunnel_key_db` (НЕ OpenSSH — см. журнал L-20261029-01) |

Проверка: `curl -s 127.0.0.1:8080/health` на VPS, `sh /opt/etc/init.d/S99monitoring status` на роутере.

**Чего больше нет:** Prometheus и Grafana жили на старом VPS и умерли вместе с ним.
Заново их не поднимать без просьбы — владелец графов не понимает.

## 12. Диагноз «Instagram тормозит» (29.10.2026)

Измерено, всё здорово: RTT роутер→VPS 76 мс (bufferbloat нет), скорость через
туннель 74 Мбит/с, VPS→Instagram 21–30 мс и 0% потерь, 40 запросов к
`i.instagram.com` через туннель — p50 0,21 с, p95 0,27 с, все 200, ошибок Hysteria
к Instagram за 2 часа — ноль, утечек напрямую — ноль.

Причина ощущения: **RTT ~100 мс** (76 до VPS + 25 до Instagram) против 20–30 мс
напрямую, несколько кругов на ленту; видео в общей сессии поднимает p95 запросов
с 0,22 до 0,46 с; плюс периодические `429` от Instagram на адрес VPS.

Вывод: лечится близким сервером (Хельсинки/Таллин, RTT 30–40 мс), а не настройками.
Отвергнутые гипотезы: IPv6 (нет default-маршрута, LAN его не получает),
bufferbloat (нет), MTU (скорость не страдает).

## 13. Не повторять: ntopng на роутере

ntopng 6.6 из Entware ставится на флешку и запускается (нужны `libndpi`,
`libsqlite3`, `libpcap`, redis с `--ignore-warnings ARM64-COW-BUG`), но захват
пакетов на `br0` **кладёт роутер**: AWG Manager и веб-интерфейс Keenetic перестают
отвечать, интернет и ping живут. Восстановление: аварийный скрипт убивает ntopng
и redis (API при этом отвечает через 30–120 с, не считать его мёртвым).
Автозапуск из `S99monitoring` убран навсегда.

## 14. Как вернуть управление, если API «умер»

1. Проверить, что интернет и ping живы, а веб Keenetic отвечает.
2. API AWGM — пробовать с таймаутом **120–150 с**: при перегрузке он отвечает
   медленно, но отвечает.
3. Если API недоступен совсем — SSH к CLI Keenetic через `plink` (пароль в
   `~/.dsh/secrets/keenetic.env`); Entware-shell на порту 222 отдельным паролем.
4. Аварийный скрипт класть через `POST /api/system/files/write`, запускать через
   `script-action` — работает даже когда панель тормозит.

## 15. Shorts: превью и остаточные паузы (29.10.2026, вечер)

### Превью (серые плитки) не грузились

Домен `yt3.ggpht.com` **блокируется по IP**: с роутера `curl` давал таймаут 10–12 с,
при этом `i.ytimg.com` работал (200 за 0.22 с). Десинк nfqws2 не помогал — это не
блокировка по SNI.

Решение — превью и манифест в рабочий туннель:

| Список | Домены | Цель |
|---|---|---|
| `list_18` «Google превью (ggpht)» | `ggpht.com`, `yt3.ggpht.com`, `yt4.ggpht.com` | `system:Proxy1` |
| `list_19` «YouTube API (манифест)» | `youtubei.googleapis.com`, `youtube-ui.l.google.com` | `system:Proxy1` |

`ggpht.com` при этом убран из `list_12`, иначе два списка матчат один домен и порядок
становится неочевидным. Результат: `yt3.ggpht.com` — 400 за 0.45 с (400 на голый GET —
это норма, главное TLS поднялся).

### Туннели

`Proxy0` (`hy2-177.177.230.47-443`) — **мёртв**: `connected:false`, SSH на
177.177.230.47 не отвечает. Работает `Proxy1` (`hy2-91.103.253.94-443`, ~179 мс).
Все активные списки переведены на `Proxy1`.

### Остаточные паузы 4–5 с в Shorts — desync не виноват

| Вариант | Слайдов с паузой >1 с (из 6) | Макс. сегмент |
|---|---|---|
| исходный (`disorder_after=-1`) | 2 | 5435 мс |
| без `disorder_after` | 2 | 5435 мс |
| + отказ TCP до `79.133.0.0/16` | 4 | 4217 мс |
| **совсем без desync для YouTube** | 3 | 5474 мс |
| метаданные через туннель | 1 | 5436 мс |
| весь YouTube через туннель | страница не грузится | — |

Вывод: это фиксированный таймаут клиента на запрос к узлу кэша провайдера
(`79.133.x`, Google Global Cache в Ростелекоме), который не отвечает. Не наш desync,
не DNS (200–250 мс), не IPv6 (нет маршрута), не QUIC (заблокирован, см. §10.2).
Если паузы вернутся: DNS-переопределение `*---sn-gvnuxaxjvh-3gg6.googlevideo.com`
в нерабочий адрес, чтобы плеер не пытался туда идти.

### Чёрный ящик

`S99yt-ring` — постоянный `tcpdump` с ротацией: файл 60 с, 12 файлов, только заголовки
(`-s 96`), в `keenetic/yt-ring/`. Разбор: `/opt/etc/yt-ring-analyze.sh`.

Важно: имя файла для `-G` обязано содержать `%Y%m%d-%H%M%S`, иначе tcpdump перезаписывает
один и тот же файл — истории не остаётся (наступил, проверил).
