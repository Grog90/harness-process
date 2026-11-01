# Анализ: почему Discord ломается на роутере, а на Windows работает

Дата: 2026-10-29. Источники: `zapret-discord-youtube-1.10.4` (Windows, работает),
`nfqws2-keenetic 1.3.2` (роутер, Discord ломается).

## 1. Как устроено на Windows

Три набора доменов и девять профилей — каждому набору своя стратегия.

| Набор | Размер | Что внутри |
|---|---|---|
| `list-general.txt` | 59 | Cloudflare, DoH-резолверы, **Discord** (23 записи), Twitch-расширения (7tv, ffz, bttv) |
| `list-google.txt` | 453 | Google, YouTube, googlevideo, ytimg, ggpht |
| `list-exclude.txt` | 129 | исключения (twitch.tv, steam, nvidia, msi …) |

Профили в `general (FAKE TLS AUTO ALT2).bat` (победитель теста, 33 OK):

| № | Фильтр | Стратегия |
|---|---|---|
| 1 | UDP 443 (QUIC), general | `fake` + `quic_initial_www_google_com.bin`, **repeats=11** |
| 2 | UDP 19294-19344, 50000-50100 | `fake` + **`ACTIVE_DISCORD_UDP.bin`**, l7=`discord,stun`, repeats=6 |
| 3 | TCP 2053,2083,2087,2096,8443 + `discord.media` | `fake,multisplit`, seqovl=681, pos=1, **badseq**, repeats=8 |
| 4 | TCP 443 + **list-google** | `ip-id=zero`, `fake,multisplit`, seqovl=681, pos=1, badseq, repeats=8 |
| 5 | TCP 80,443 + **list-general** | `fake,multisplit`, seqovl=681, pos=1, **badseq**, repeats=8, tls_mod=rnd,dupsid,sni=www.google.com |
| 6–9 | ipset-all, игровые порты | аналогично, для остального |

Ключевое: **Google и Discord обрабатываются разными стратегиями**. У Google —
`ip-id=zero` и без tls_mod; у Discord/general — `badseq` + `fake-tls-mod`
+ `repeats=8`.

## 2. Как устроено на роутере сейчас

| Что | Факт |
|---|---|
| Списков | **один** `user.list`, 273 записи: 171 YouTube/Google + 22 Discord вперемешку |
| TCP-профиль | **один**: `fake` + `multisplit` (pos=1, seqovl=681), **без badseq, без repeats** |
| QUIC-профиль | `fake` + `quic_initial`, 11 повторов |
| UDP-профиль | порты включают 19294-19344, но блоб — `quic_initial`, **не Discord-блоб** |
| Профиль для `discord.media` на портах 2053/2083/2087/2096/8443 | **отсутствует** |

## 3. Диагноз

Роутер применяет к Discord **стратегию для Google** (без `badseq`, без
`fake-tls-mod`, без повторов), а к YouTube — ту же самую. Windows же разводит
их по разным спискам и разным параметрам.

Прямое следствие: у Discord ломается часть TLS-рукопожатий — в браузере это
`ERR_SSL_VERSION_OR_CIPHER_MISMATCH` / `ERR_CERT_VERIFIER_CHANGED`, в
приложении — зависание на `Discord Updater`, потому что лаунчер не может
дозагрузить свои ресурсы.

Проверено: при возврате Discord в туннель страница открывается без ошибок
(1067 ответов 200, 0 проблем), при прямой маршрутизации через nfqws2 —
сотни SSL-ошибок.

## 4. Что нужно сделать (план)

Цель — воспроизвести на роутере **разделение**, которое есть на Windows.

**Шаг 1. Блобы.** Скопировать с ПК на роутер:
`ACTIVE_DISCORD_UDP.bin`, `tls_clienthello_www_google_com.bin`,
`quic_initial_www_google_com.bin`, `stun.bin`.
Проверка: файлы на месте, размеры совпадают.

**Шаг 2. Разделить списки.** Вместо одного `user.list`:
`list-general.list` (из Windows `list-general.txt` — Discord, Cloudflare, DoH)
и `list-google.list` (YouTube/Google из текущего `user.list`).
Проверка: `wc -l` обоих, домены не пересекаются.

**Шаг 3. Профили в `nfqws2.conf`** — по образцу Windows:

```
# general (Discord, Cloudflare) — TCP 80,443
--filter-tcp=80,443 --hostlist=list-general.list --filter-l7=http,tls
--lua-desync=fake:blob=tls_clienthello:tls_mod=rnd,dupsid,sni=www.google.com:repeats=8
--lua-desync=multisplit:pos=1:seqovl=681:seqovl_pattern=tls_clienthello:fooling=badseq
--new
# google (YouTube) — TCP 443
--filter-tcp=443 --hostlist=list-google.list --filter-l7=tls
--lua-desync=fake:blob=tls_clienthello:tls_mod=rnd,dupsid:sni=www.google.com:repeats=8
--lua-desync=multisplit:pos=1:seqovl=681:seqovl_pattern=tls_clienthello:fooling=badseq
--new
# discord.media на альтернативных портах
--filter-tcp=2053,2083,2087,2096,8443 --hostlist-domains=discord.media
--lua-desync=fake:blob=tls_clienthello:repeats=8
--lua-desync=multisplit:pos=1:seqovl=681:seqovl_pattern=tls_clienthello:fooling=badseq
--new
# QUIC
--filter-udp=443 --filter-l7=quic --lua-desync=fake:blob=quic_initial:repeats=11
--new
# голос Discord
--filter-udp=19294-19344,50000-50100 --filter-l7=discord,stun
--lua-desync=fake:blob=discord_udp:repeats=6
```

Точные имена аргументов Lua-функций сверить с `docs/manual.md` zapret2
(`fooling`, `repeats`, `tls_mod`, `seqovl`, `seqovl_pattern`) — это отдельная
проверка перед запуском, `--dry-run` у nfqws2 для этого и есть.

**Шаг 4. Маршрут Discord** — `list_4` перевести в `wan:ppp0` (напрямую).

**Шаг 5. Проверка, в этом порядке:**
1. `nfqws2 --dry-run` — конфиг валиден;
2. сервис стартует, правила NFQUEUE на месте;
3. `chrome_net` на `discord.com/app` — 0 ошибок TLS;
4. запуск приложения: исчезает окно `Discord Updater`, появляется главное окно;
5. `chrome_eval` на YouTube — видео играет (`currentTime` растёт);
6. голос Discord — ваш тест.

## 7. Обновление: честные тесты 14:56 и победитель ALT3

Тесты перезапущены **с выключенным VPN для Discord**, то есть без искажений.
Победитель — `general (ALT3).bat`: **33 OK, 3 ERR**, все четыре Discord-цели
зелёные (`DiscordMain`, `DiscordGateway`, `DiscordCDN`, `DiscordUpdates`),
YouTube-картинки OK. Это совпадает с опытом владельца: «вчера ALT3 работал
хорошо».

ALT2, который был лучшим в прошлом прогоне, при честных условиях даёт 21 OK.

### Чем ALT3 отличается технически

| | ALT2 (multisplit) | **ALT3 (hostfakesplit)** |
|---|---|---|
| Приём TCP | `multisplit` + `seqovl=681` | **`hostfakesplit`** |
| Подмена хоста | `fake-tls-mod=rnd,dupsid,sni=www.google.com` | `fake-tls-mod=rnd,dupsid,sni=**ya.ru**` (general) / `www.google.com` (google) |
| Fooling | `badseq` + инкремент | **`fooling=ts`** (timestamp) |
| Повторы | `repeats=8` | `repeats=6` |
| Доп. порты Discord | `discord.media` на 2053/2083/2087/2096/8443 | то же, но через `hostfakesplit` |
| Google | без особого флага | **`ip-id=zero`** |

Профили ALT3 (строки 17–25 файла):

1. UDP 443 + `list-general` → `fake` + `quic_initial_www_google_com.bin`, repeats=6
2. UDP 19294-19344,50000-50100, l7=discord,stun → `fake` + `ACTIVE_DISCORD_UDP.bin`, repeats=6
3. TCP 2053/2083/2087/2096/8443 + `discord.media` → `fake,hostfakesplit`, sni=www.google.com, **fooling=ts**
4. TCP 443 + `list-google` → то же + **`ip-id=zero`**
5. TCP 80,443 + `list-general` → `fake,hostfakesplit`, sni=**ya.ru**, fooling=ts, + `fake-http`
6–9. ipset-профили и игровые порты

**Роутер сейчас использует multisplit из ALT2** — то есть технику, которая
Discord и ломает. Отсюда 386 SSL-ошибок и зависший `Discord Updater`.

### Поддержка в nfqws2 (проверено по исходникам пакета)

| Нужно | В nfqws2 есть |
|---|---|
| `hostfakesplit` | да — `--lua-desync=hostfakesplit:host=<host>` (пример в дефолтном конфиге: `host=ozon.ru`) |
| `fooling=ts` | аналог — флаг `tcp_ts_up` (комментарий в `zapret-lib.lua`: «move timestamp tcp option to the top») |
| `fake-tls-mod` | `tls_mod=rnd,dupsid,sni=<host>` |
| `repeats` | стандартный аргумент `repeats=N` |
| `ip-id=zero` | стандартный аргумент `ip_id=zero` |

То есть ALT3 переносится на роутер один в один, но **другими именами
аргументов**, и требует блобов из `bin/` вашей сборки.

### Что это меняет в плане

Шаги те же, но содержимое профилей — из ALT3, а не ALT2:

- блобы: `ACTIVE_DISCORD_UDP.bin`, `quic_initial_www_google_com.bin`,
  `tls_clienthello_www_google_com.bin`, `tls_clienthello_www_sferum_ru.bin`;
- профиль Discord/general: `fake` + `hostfakesplit` с `host=ya.ru`, `tcp_ts_up`;
- профиль Google: то же с `host=www.google.com`, `ip_id=zero`;
- перед запуском — `nfqws2 --dry-run` (проверяет аргументы, ничего не включает).

## 5. Правила выполнения

- каждый шаг — отдельно, с показом результата **до** следующего;
- при неудаче — **стоп и доклад**, без самостоятельного отката;
- бэкапы: `user.list.bak`, `nfqws2.conf.bak` (уже есть), плюс копия перед
  разделением списков;
- откат — только по прямой просьбе.

## 6. Чего в этом плане нет

- `MODE_ALL` (перехват всего трафика) — не включаем;
- добавления доменов «на глаз» — только из проверенных списков Windows;
- изменения маршрутов других сервисов — не трогаем.

## 8. Итог (29.10.2026, выполнено)

Конфиг ALT3 перенесён и работает. Итоговая схема на роутере:

```
YouTube  → nfqws2: fake(sni=www.google.com, tcp_ts=-600000)
                    + hostfakesplit(host=www.google.com, disorder_after=-1,
                      tcp_ts=-600000, ip_id=zero), список list-google.list (22)
Discord  → nfqws2: тот же профиль general (sni=ya.ru), список list-general.list (59)
         → голос: UDP 19294-19344,50000-50100 + ACTIVE_DISCORD_UDP.bin, repeats=6
         → discord.media: альт-порты 2053/2083/2087/2096/8443
Остальное → Hysteria / Proxy0 (списки AWGM)
```

Результаты:

| Проверка | Результат |
|---|---|
| YouTube страница / видео | 200 / `playing:true`, 854×480, буфер 49 с |
| Discord web | 1067×200, **0 ошибок TLS** (было 386) |
| Discord app | окно открылось, `Discord Updater` не зависает |
| Discord голос | 69 мс средняя, 0.0 % потерь |

Ключевые правила переноса — в скилле `.dsh/skills/vpn-router/SKILL.md`,
инвентарь и история тупиков — в `reference.md` рядом с ним.

