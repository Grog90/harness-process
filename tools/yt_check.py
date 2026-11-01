#!/usr/bin/env python3
"""yt_check.py — автономная проверка YouTube настоящим Chrome.

Что проверяет:
  * интерфейс: главная страница, превью, аватарки;
  * обычное видео: воспроизведение, буфер, дропнутые кадры, зависания;
  * Shorts: воспроизведение, листание на следующий, метрики плеера;
  * сеть: статусы, упавшие запросы, проблемные хосты.

Запуск:
    python tools/yt_check.py            # все проверки
    python tools/yt_check.py --video    # только обычное видео
    python tools/yt_check.py --shorts   # только Shorts

Печатает markdown-сводку и сохраняет JSON в .browser/yt-check-<ts>.json.
Только стандартная библиотека, движок — tools/chrome_cdp.py.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import chrome_cdp  # noqa: E402

HOME = "https://www.youtube.com/"
VIDEO = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
SHORTS = "https://www.youtube.com/shorts/"

#: JS для замера воспроизведения: сколько реально проиграно, буфер, дропы, зависания.
PLAY_JS = """
(async () => {
  const v = document.querySelector('video');
  if (!v) return JSON.stringify({error: 'нет элемента <video>'});
  const m = {waiting: 0, stalled: 0};
  v.addEventListener('waiting', () => m.waiting++);
  v.addEventListener('stalled', () => m.stalled++);
  v.muted = true;
  v.play().catch(() => {});
  const t0 = v.currentTime;
  await new Promise(r => setTimeout(r, %d));
  const q = v.getVideoPlaybackQuality ? v.getVideoPlaybackQuality() : {};
  return JSON.stringify({
    played: +(v.currentTime - t0).toFixed(2),
    currentTime: +v.currentTime.toFixed(2),
    duration: v.duration ? +v.duration.toFixed(1) : null,
    width: v.videoWidth, height: v.videoHeight,
    buffered: v.buffered.length ? +v.buffered.end(v.buffered.length - 1).toFixed(1) : 0,
    dropped: q.droppedVideoFrames ?? null,
    totalFrames: q.totalVideoFrames ?? null,
    waiting: m.waiting, stalled: m.stalled,
    paused: v.paused
  });
})()
"""

UI_JS = """
JSON.stringify({
  title: document.title,
  tiles: document.querySelectorAll('a#thumbnail, ytd-rich-item-renderer').length,
  shortsLinks: document.querySelectorAll('a[href^="/shorts/"]').length,
  images: [...document.images].filter(i => (i.currentSrc || i.src)).length,
  loadedImages: [...document.images].filter(i => i.naturalWidth > 0).length,
  bodyText: (document.body ? document.body.innerText.length : 0)
})
"""


def _json(value: str | None) -> dict:
    if not value:
        return {"error": "empty result"}
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return {"raw": value}


def check_home() -> dict:
    started = time.time()
    with chrome_cdp.Session(HOME) as s:
        s.drain(12)
        ui = _json(s.eval(UI_JS))
        shot = s.shot("yt-check-home")
        net = s.net()
    return {
        "name": "home",
        "seconds": round(time.time() - started, 1),
        "ui": ui,
        "net": net,
        "screenshot": shot,
        "ok": (ui.get("loadedImages", 0) > 0 and ui.get("tiles", 0) > 0
               and net.get("problems_count", 99) < 50),
    }


def check_video(url: str = VIDEO, seconds: int = 10) -> dict:
    started = time.time()
    with chrome_cdp.Session(url) as s:
        s.drain(10)
        play = _json(s.eval(PLAY_JS % (seconds * 1000)))
        shot = s.shot("yt-check-video")
        net = s.net()
    dropped = play.get("dropped")
    total = play.get("totalFrames") or 0
    drop_ratio = (dropped / total) if isinstance(dropped, int) and total else 0.0
    ok = (
        play.get("played", 0) >= seconds * 0.5
        and drop_ratio <= 0.05
        and play.get("waiting", 99) <= 2
    )
    return {
        "name": "video",
        "seconds": round(time.time() - started, 1),
        "play": play,
        "drop_ratio": round(drop_ratio, 4),
        "net": net,
        "screenshot": shot,
        "ok": ok,
    }


SHORTS_INFO_JS = """
JSON.stringify({
  url: location.href,
  shorts: document.querySelectorAll('ytd-reel-video-renderer').length,
  active: (() => {
    const el = document.querySelector('ytd-reel-video-renderer[is-active]');
    return el ? el.getAttribute('data-index') || 'active' : null;
  })(),
  hasVideo: !!document.querySelector('video')
})
"""

SHORTS_NEXT_JS = """
(() => {
  const btn = document.querySelector('button[aria-label*="Next"], #navigation-button-down button');
  if (btn) { btn.click(); return 'button'; }
  const c = document.querySelector('#shorts-container, ytd-shorts');
  if (c) { c.scrollBy(0, window.innerHeight); return 'scroll'; }
  return 'none';
})()
"""


def check_shorts(seconds: int = 8) -> dict:
    started = time.time()
    with chrome_cdp.Session(SHORTS) as s:
        s.drain(12)
        info = _json(s.eval(SHORTS_INFO_JS))
        first = _json(s.eval(PLAY_JS % (seconds * 1000)))
        shot1 = s.shot("yt-check-shorts-1")
        moved = s.eval(SHORTS_NEXT_JS)
        s.drain(6)
        second = _json(s.eval(PLAY_JS % (seconds * 1000)))
        shot2 = s.shot("yt-check-shorts-2")
        info2 = _json(s.eval(SHORTS_INFO_JS))
        net = s.net()
    ok = (
        first.get("played", 0) >= seconds * 0.4
        and second.get("played", 0) >= seconds * 0.3
        and moved != "none"
    )
    return {
        "name": "shorts",
        "seconds": round(time.time() - started, 1),
        "first": first,
        "after_swipe": second,
        "swipe": moved,
        "page_first": info,
        "page_after": info2,
        "net": net,
        "screenshots": [shot1, shot2],
        "ok": ok,
    }


def _line(label: str, value: object) -> str:
    return f"| {label} | {value} |"


def report(results: list[dict]) -> str:
    out = ["# Проверка YouTube", ""]
    for r in results:
        out.append(f"## {r['name']} — {'OK' if r['ok'] else 'ПРОБЛЕМА'}")
        out.append("")
        out.append("| Метрика | Значение |")
        out.append("|---|---|")
        if r["name"] == "home":
            ui = r["ui"]
            out.append(_line("заголовок", ui.get("title")))
            out.append(_line("плиток на странице", ui.get("tiles")))
            out.append(_line("ссылок на Shorts", ui.get("shortsLinks")))
            out.append(_line("картинок загружено", f"{ui.get('loadedImages')}/{ui.get('images')}"))
        elif r["name"] == "video":
            p = r["play"]
            out.append(_line("проиграно за 10 с", f"{p.get('played')} с"))
            out.append(_line("разрешение", f"{p.get('width')}x{p.get('height')}"))
            out.append(_line("буфер", f"{p.get('buffered')} с"))
            out.append(_line("дропнуто кадров", f"{p.get('dropped')} из {p.get('totalFrames')} ({r['drop_ratio']*100:.2f}%)"))
            out.append(_line("зависаний (waiting)", p.get("waiting")))
            out.append(_line("ошибка плеера", p.get("error") or "нет"))
        elif r["name"] == "shorts-scroll":
            out.append("| # | до кадра | waiting | проиграно | буфер | сегменты | дропы |")
            out.append("|---|---|---|---|---|---|---|")
            for it in r["slides"]:
                out.append(f"| {it.get('index')} | {it.get('load_to_frame_ms')} мс | {it.get('waiting_ms')} мс | "
                           f"{it.get('played')} с | {it.get('buffered')} с | "
                           f"{it.get('segments')} (ср {it.get('seg_avg_ms')} / макс {it.get('seg_max_ms')} мс) | "
                           f"{it.get('dropped')} |")
        else:
            for key, label in (("first", "первый Shorts"), ("after_swipe", "после листания")):
                p = r[key]
                out.append(_line(label, f"проиграно {p.get('played')} с, буфер {p.get('buffered')} с, "
                                         f"waiting {p.get('waiting')}, дропы {p.get('dropped')}"))
            out.append(_line("листание", r.get("swipe")))
            out.append(_line("Shorts в DOM", f"{r['page_first'].get('shorts')} → {r['page_after'].get('shorts')}"))
        net = r.get("net", {})
        out.append(_line("сетевые проблемы", net.get("problems_count")))
        out.append(_line("статусы", net.get("statuses")))
        shots = r.get("screenshots") or [r.get("screenshot")]
        out.append(_line("скриншоты", ", ".join(str(x) for x in shots if x)))
        out.append("")
    verified = [r for r in results if r["ok"]]
    out.append(f"**Итого: {len(verified)} из {len(results)} проверок пройдено.**")
    return "\n".join(out)


SHORTS_STEP_JS = """
(async () => {
  const v = document.querySelector('video');
  if (!v) return JSON.stringify({error: 'нет элемента <video>'});
  const t0 = performance.now();
  const srcBefore = v.currentSrc;
  let firstFrame = null, waitStart = null, waitTotal = 0, loadStart = null;
  const onLoadStart = () => { if (loadStart === null) loadStart = performance.now(); };
  const onPlaying = () => {
    if (firstFrame === null) firstFrame = performance.now();
    if (waitStart !== null) { waitTotal += performance.now() - waitStart; waitStart = null; }
  };
  const onWaiting = () => { if (waitStart === null) waitStart = performance.now(); };
  v.addEventListener('loadstart', onLoadStart);
  v.addEventListener('playing', onPlaying);
  v.addEventListener('waiting', onWaiting);
  v.muted = true;
  try { await v.play(); } catch (e) { /* автоплей может быть запрещён */ }
  const t1 = v.currentTime;
  // ждём подмены источника (новый ролик) или 6 секунд
  const deadline = performance.now() + 6000;
  while (performance.now() < deadline) {
    await new Promise(r => setTimeout(r, 100));
    if (v.currentSrc && srcBefore && v.currentSrc !== srcBefore) break;
  }
  await new Promise(r => setTimeout(r, 4000));
  v.removeEventListener('loadstart', onLoadStart);
  v.removeEventListener('playing', onPlaying);
  v.removeEventListener('waiting', onWaiting);
  const q = v.getVideoPlaybackQuality ? v.getVideoPlaybackQuality() : {};
  const seg = performance.getEntriesByType('resource')
    .filter(e => /googlevideo/.test(e.name) && e.startTime >= t0 - 100);
  let segTotal = 0, segMax = 0;
  for (const e of seg) { segTotal += e.duration; if (e.duration > segMax) segMax = e.duration; }
  const slow = seg.filter(e => e.duration > 1000)
    .map(e => ({ host: new URL(e.name).host, ms: Math.round(e.duration),
                 kb: Math.round((e.transferSize || 0) / 1024) }));
  return JSON.stringify({
    load_to_frame_ms: firstFrame !== null ? Math.round(firstFrame - t0) : null,
    waiting_ms: Math.round(waitTotal),
    played: +(v.currentTime - t1).toFixed(2),
    width: v.videoWidth, height: v.videoHeight,
    buffered: v.buffered.length ? +v.buffered.end(v.buffered.length - 1).toFixed(1) : 0,
    segments: seg.length,
    seg_avg_ms: seg.length ? Math.round(segTotal / seg.length) : null,
    seg_max_ms: segMax ? Math.round(segMax) : null,
    slow_segments: slow,
    dropped: q.droppedVideoFrames ?? null,
    srcChanged: !!(v.currentSrc && srcBefore && v.currentSrc !== srcBefore)
  });
})()
"""


def check_shorts_scroll(slides: int = 5, seconds: int = 5) -> dict:
    """Пролистать несколько Shorts подряд, замеряя каждый."""
    started = time.time()
    items: list[dict] = []
    shots: list[str] = []
    with chrome_cdp.Session(SHORTS) as s:
        s.drain(12)
        for i in range(slides):
            step = _json(s.eval(SHORTS_STEP_JS))
            step["index"] = i + 1
            items.append(step)
            shots.append(s.shot(f"yt-check-shorts-{i + 1}"))
            if i < slides - 1:
                s.eval(SHORTS_NEXT_JS)
                s.drain(3)
        net = s.net()
    played = [it.get("played", 0) for it in items]
    waits = [it.get("waiting_ms", 0) or 0 for it in items]
    loads = [it.get("load_to_frame_ms") or 0 for it in items]
    ok = (len(items) == slides
          and all(p >= seconds * 0.4 for p in played)
          and max(waits, default=0) <= 1500
          and max(loads, default=0) <= 3000)
    return {
        "name": "shorts-scroll",
        "seconds": round(time.time() - started, 1),
        "slides": items,
        "net": net,
        "screenshots": shots,
        "ok": ok,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Проверка YouTube настоящим Chrome")
    parser.add_argument("--video", action="store_true", help="только обычное видео")
    parser.add_argument("--shorts", action="store_true", help="только Shorts")
    parser.add_argument("--home", action="store_true", help="только главная")
    parser.add_argument("--slides", type=int, default=0,
                        help="пролистать N Shorts подряд (вместо одиночной проверки)")
    parser.add_argument("--json", action="store_true", help="вывести JSON")
    args = parser.parse_args()

    only = args.video or args.shorts or args.home
    results: list[dict] = []
    if not only or args.home:
        results.append(check_home())
    if not only or args.video:
        results.append(check_video())
    if args.slides:
        results.append(check_shorts_scroll(args.slides))
    elif not only or args.shorts:
        results.append(check_shorts())

    out_dir = Path(chrome_cdp.SHOT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    dump = out_dir / f"yt-check-{time.strftime('%Y%m%d-%H%M%S')}.json"
    dump.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        print(report(results))
        print(f"\nJSON: {dump}")
    return 0 if all(r["ok"] for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
