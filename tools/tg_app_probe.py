#!/usr/bin/env python3
"""tg_app_probe.py — довести форму создания приложения на my.telegram.org до ответа.

Зачем: вход требует кода из Telegram, и его может ввести только владелец аккаунта.
Скрипт открывает видимое окно Chrome, ждёт, пока человек войдёт, и дальше САМ
заполняет форму и жмёт кнопку — и печатает ровно то, что ответил сервер.

Запуск:  python tools/tg_app_probe.py [--title ...] [--shortname ...]
Ждёт входа до --login-timeout секунд (по умолчанию 600).

Вывод — строки JSON: status, ждём вход / залогинен / форма отправлена / ответ.
"""

from __future__ import annotations

import argparse
import json
import random
import string
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import chrome_cdp  # noqa: E402

APPS = "https://my.telegram.org/apps"


def say(**kw: object) -> None:
    print(json.dumps(kw, ensure_ascii=False), flush=True)


def rand(n: int = 15) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(random.choice(alphabet) for _ in range(n))


def main() -> int:
    ap = argparse.ArgumentParser()
    # Рецепт из habr.com/ru/articles/923168: только латинские буквы, UpperCamelCase,
    # title и short name одинаковые, поле URL — рабочий сайт, описание 40+ символов.
    ap.add_argument("--title", default="MonitorMyChats")
    ap.add_argument("--shortname", default="MonitorMyChats")
    ap.add_argument("--url", default="https://example.com")
    ap.add_argument("--desc", default="Personal client application for reading my own Telegram chats.")
    ap.add_argument("--login-timeout", type=int, default=600)
    ap.add_argument("--hold", type=int, default=8, help="сколько держать окно после ответа")
    args = ap.parse_args()

    title = args.title
    shortname = args.shortname
    say(event="start", title=title, shortname=shortname, url=args.url, desc=args.desc,
        profile=chrome_cdp._settings()["profile"])

    chrome = chrome_cdp.Chrome()
    try:
        session = chrome_cdp._open(chrome, APPS, 1280, 900)
        chrome.drain(4)

        # 1. Ждём, пока человек войдёт: /auth → любая другая страница.
        deadline = time.time() + args.login_timeout
        authed = False
        while time.time() < deadline:
            path = chrome_cdp._eval(chrome, session, "location.pathname") or ""
            if path and not path.startswith("/auth"):
                authed = True
                break
            say(event="waiting_login", path=path)
            chrome.drain(3)
        if not authed:
            say(event="login_timeout", seconds=args.login_timeout)
            return 2
        say(event="logged_in")
        chrome.drain(3)

        # 1.5. Сначала смотрим, что уже есть в кабинете: приложение могло быть
        # создано раньше, и тогда форма — не то, что нам нужно.
        pre = chrome_cdp._eval(chrome, session, "document.body ? document.body.innerText : ''") or ""
        say(event="apps_page_before", text=pre.strip()[:1200])
        low = pre.lower()
        if "app api_id" in low or "app_api_id" in low or "api_id:" in low:
            say(event="existing_app_likely", hint="на странице уже есть api_id — форма не нужна")
            return 0

        # 2. Заполняем форму (если она уже на экране).
        fill = """
        (() => {
          const set = (name, value) => {
            const el = document.querySelector(`[name="${name}"], #${name}`);
            if (!el) return false;
            el.focus();
            el.value = value;
            el.dispatchEvent(new Event('input', {bubbles: true}));
            el.dispatchEvent(new Event('change', {bubbles: true}));
            return true;
          };
          const ok = {};
          ok.title = set('app_title', %s);
          ok.shortname = set('app_shortname', %s);
          ok.url = set('app_url', %s);
          ok.desc = set('app_desc', %s);
          const plat = document.querySelector('[name="app_platform"], #app_platform');
          if (plat) { plat.value = 'desktop';
            plat.dispatchEvent(new Event('change', {bubbles: true})); ok.platform = true; }
          const btn = document.querySelector('button[type="submit"]')
                   || [...document.querySelectorAll('button,a')]
                        .find(b => /create application|создать/i.test(b.innerText || ''));
          ok.button = btn ? (btn.innerText || '').trim().slice(0, 40) : null;
          if (btn) btn.click();
          return JSON.stringify(ok);
        })()
        """ % (
            json.dumps(title),
            json.dumps(shortname),
            json.dumps(args.url),
            json.dumps(args.desc),
        )
        filled = chrome_cdp._eval(chrome, session, fill)
        say(event="form_filled", result=filled)

        # 3. Собираем ответ сервера: и текст страницы, и сетевые ответы.
        chrome.drain(8)
        body = chrome_cdp._eval(chrome, session, "document.body ? document.body.innerText : ''") or ""
        path = chrome_cdp._eval(chrome, session, "location.pathname") or ""
        title_now = chrome_cdp._eval(chrome, session, "document.title") or ""
        posts = []
        for event in chrome.events:
            if event.get("method") == "Network.responseReceived":
                resp = event["params"]["response"]
                if "/apps" in resp.get("url", ""):
                    posts.append({"status": resp.get("status"), "url": resp["url"][:160]})
        say(event="result", path=path, title=title_now,
            body=body.strip()[:1500], responses=posts)
        time.sleep(args.hold)
        return 0
    finally:
        chrome.close()


if __name__ == "__main__":
    raise SystemExit(main())
