#!/usr/bin/env python3
"""Render the plugin locally the way TRMNL does: Liquid -> HTML -> screenshot -> 1-bit.

  pip install python-liquid playwright pillow && playwright install chromium
  python3 tools/preview.py --loc 31.52,74.36 --label Lahore --tz Asia/Karachi
  python3 tools/preview.py --url "https://your-host/sky?loc=51.48,0"

Writes preview/<layout>.html, preview/<layout>.png (grayscale) and
preview/<layout>_1bit.png (what an OG TRMNL panel shows) + preview/index.html.
"""
import argparse
import json
import re
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from liquid import DictLoader, Environment

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "plugin" / "src"
CACHE = ROOT / "tools" / ".cache"
OUT = ROOT / "preview"

CSS_URL = "https://trmnl.com/css/latest/plugins.css"
JS_URL = "https://trmnl.com/js/latest/plugins.js"

LAYOUTS = {
    # name: (mashup class, view count)
    "full": (None, 1),
    "half_horizontal": ("mashup--1Tx1B", 2),
    "half_vertical": ("mashup--1Lx1R", 2),
    "quadrant": ("mashup--2x2", 4),
}

TEMPLATE_RE = re.compile(r"{%-?\s*template\s+(\S+)\s*-?%}(.*?){%-?\s*endtemplate\s*-?%}", re.S)


def cached(url, name):
    CACHE.mkdir(parents=True, exist_ok=True)
    p = CACHE / name
    if not p.exists():
        print(f"downloading {url}")
        req = urllib.request.Request(url, headers={"User-Agent": "trmnl-night-sky-preview"})
        data = urllib.request.urlopen(req, timeout=60).read()
        if name.endswith(".css"):  # fonts are referenced root-relative
            data = data.replace(b'url("/fonts/', b'url("https://trmnl.com/fonts/')
        p.write_bytes(data)
    return p


def get_payload(a):
    if a.url:
        with urllib.request.urlopen(a.url, timeout=20) as r:
            return json.load(r)
    sys.path.insert(0, str(ROOT / "backend"))
    import server
    q = {"loc": a.loc, "label": a.label, "mode": a.mode, "clouds": "yes" if a.clouds else "no"}
    if a.tz:
        q["tz"] = a.tz
    if a.time:
        q["t"] = a.time
    return server.handle_sky(q)


def render_views(payload):
    shared = (SRC / "shared.liquid").read_text()
    partials = {m.group(1): m.group(2) for m in TEMPLATE_RE.finditer(shared)}
    shared_rest = TEMPLATE_RE.sub("", shared)
    env = Environment(loader=DictLoader(partials))
    data = dict(payload)
    data["trmnl"] = {"plugin_settings": {"instance_name": "Night Sky"}}
    out = {}
    for name in LAYOUTS:
        src = shared_rest + (SRC / f"{name}.liquid").read_text()
        out[name] = env.from_string(src).render(**data)
    return out


def page(name, body, css, js):
    mashup, n = LAYOUTS[name]
    view = f'<div class="view view--{name}">{body}</div>'
    inner = view if not mashup else f'<div class="mashup {mashup}">' + view * n + "</div>"
    return f"""<!doctype html><html><head><meta charset="utf-8">
<link rel="stylesheet" href="{css}"><script src="{js}"></script>
<style>html,body{{margin:0;width:800px;height:480px;overflow:hidden}}</style></head>
<body class="environment trmnl"><div class="screen">{inner}</div></body></html>"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url")
    ap.add_argument("--loc", default="51.48,0")
    ap.add_argument("--label", default="")
    ap.add_argument("--tz", default="")
    ap.add_argument("--mode", default="auto")
    ap.add_argument("--time", help="ISO instant, e.g. 2026-11-01T16:30:00Z")
    ap.add_argument("--no-clouds", dest="clouds", action="store_false")
    ap.add_argument("--no-png", dest="png", action="store_false")
    a = ap.parse_args()

    payload = get_payload(a)
    OUT.mkdir(exist_ok=True)
    (OUT / "payload.json").write_text(json.dumps(payload, indent=1, ensure_ascii=False))
    print(f"payload: {len(json.dumps(payload, separators=(',', ':'))) // 1024} KB")
    css = cached(CSS_URL, "plugins.css").resolve().as_uri()
    js = cached(JS_URL, "plugins.js").resolve().as_uri()
    views = render_views(payload)
    for name, body in views.items():
        (OUT / f"{name}.html").write_text(page(name, body, css, js))

    if a.png:
        from PIL import Image
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            b = pw.chromium.launch()
            pg = b.new_page(viewport={"width": 800, "height": 480})
            for name in views:
                pg.goto((OUT / f"{name}.html").resolve().as_uri())
                pg.wait_for_load_state("networkidle")
                pg.wait_for_timeout(300)
                png = OUT / f"{name}.png"
                pg.screenshot(path=str(png))
                Image.open(png).convert("L").point(lambda v: 255 if v > 127 else 0).convert("1").save(OUT / f"{name}_1bit.png")
                print(f"wrote preview/{name}_1bit.png")
            b.close()

    cards = "".join(f'<figure><img src="{n}_1bit.png"><figcaption>{n}</figcaption></figure>' for n in LAYOUTS)
    (OUT / "index.html").write_text(
        "<!doctype html><meta charset=utf-8><title>Night Sky preview</title>"
        "<style>body{font:14px system-ui;background:#ddd;display:flex;flex-wrap:wrap;gap:20px;padding:20px}"
        "img{width:800px;image-rendering:pixelated;border:8px solid #333;border-radius:6px}</style>" + cards)


if __name__ == "__main__":
    main()
