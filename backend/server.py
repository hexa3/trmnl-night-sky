#!/usr/bin/env python3
"""Night Sky - TRMNL plugin backend. Standard library only.

  GET /sky?loc=31.52,74.36            JSON payload for the TRMNL polling strategy
       &tz=Asia/Karachi                (optional; auto-detected via Open-Meteo, else UTC)
       &label=Lahore                   (optional; shown in the title bar)
       &mode=auto|now                  (auto: show tonight's sky during daytime)
       &clock=24|12
       &clouds=yes|no                  (Open-Meteo cloud forecast, default yes)
       &t=2026-11-01T20:30:00Z         (optional; render a specific instant)
  GET /healthz

Run:  PORT=8080 python3 server.py
"""
import json
import math
import os
import sys
import threading
import time
import traceback
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import astro  # noqa: E402
import sky    # noqa: E402

FORECAST_TTL = 45 * 60
_cache = {}
_lock = threading.Lock()


def fetch_forecast(lat, lon):
    """Hourly cloud cover + IANA timezone from Open-Meteo (free, no key). Cached."""
    key = (round(lat, 1), round(lon, 1))
    with _lock:
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < FORECAST_TTL:
            return hit[1]
    url = ("https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode({
        "latitude": f"{lat:.3f}", "longitude": f"{lon:.3f}",
        "hourly": "cloud_cover", "timezone": "auto",
        "timeformat": "unixtime", "forecast_days": 3, "past_days": 1,
    }))
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "trmnl-night-sky/1.0"})
        with urllib.request.urlopen(req, timeout=4) as r:
            data = json.load(r)
        times = data["hourly"]["time"]
        cover = data["hourly"]["cloud_cover"]
        series = [(t / 86400.0 + 2440587.5, c) for t, c in zip(times, cover) if c is not None]
        result = {"tz": data.get("timezone"), "clouds": series}
    except Exception as e:  # network down, rate limited... the chart still works
        print(f"open-meteo failed: {e}", file=sys.stderr)
        result = {"tz": None, "clouds": None}
    with _lock:
        _cache[key] = (time.time(), result)
    return result


def parse_loc(q):
    loc = q.get("loc") or q.get("lat_lon") or ""
    if loc and "," in loc:
        a, b = loc.split(",", 1)
        return float(a), float(b)
    return float(q["lat"]), float(q["lon"])


def guess_tz(lon):
    """Last-resort fixed-offset zone from longitude (Etc/GMT signs are inverted)."""
    off = int(round(lon / 15.0))
    return "UTC" if off == 0 else f"Etc/GMT{'-' if off > 0 else '+'}{abs(off)}"


def handle_sky(q):
    lat, lon = parse_loc(q)
    if not (-90 <= lat <= 90 and -180 <= lon <= 180) or math.isnan(lat) or math.isnan(lon):
        raise ValueError("lat/lon out of range")
    want_clouds = q.get("clouds", "yes").lower() not in ("no", "false", "0", "off")
    tz = (q.get("tz") or "").strip()
    fc = fetch_forecast(lat, lon) if (want_clouds or not tz) else {"tz": None, "clouds": None}
    if tz:
        try:
            ZoneInfo(tz)
        except (ZoneInfoNotFoundError, ValueError):
            tz = ""
    tz = tz or fc.get("tz") or guess_tz(lon)
    when = None
    if q.get("t"):
        when = datetime.fromisoformat(q["t"].replace("Z", "+00:00"))
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
    mode = "now" if q.get("mode", "auto").lower() in ("now", "live") else "auto"
    return sky.build(
        lat, lon, tz, when=when, mode=mode,
        label=q.get("label", "").strip()[:40],
        clock24=q.get("clock", "24") != "12",
        clouds=fc.get("clouds") if want_clouds else None,
    )


class Handler(BaseHTTPRequestHandler):
    server_version = "NightSky/1.0"

    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "public, max-age=300")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = {k: v[-1] for k, v in urllib.parse.parse_qs(u.query).items()}
        if u.path in ("/healthz", "/health"):
            return self._send(200, '{"ok":true}')
        if u.path == "/sky":
            try:
                payload = handle_sky(q)
            except (KeyError, ValueError) as e:
                return self._send(400, json.dumps({"error": f"bad request: {e}. Try /sky?loc=51.5,-0.12"}))
            except Exception:
                traceback.print_exc()
                return self._send(500, '{"error":"internal error"}')
            return self._send(200, json.dumps(payload, separators=(",", ":"), ensure_ascii=False))
        if u.path == "/":
            return self._send(200, LANDING, "text/html")
        self._send(404, '{"error":"not found"}')

    def log_message(self, fmt, *args):
        sys.stderr.write("%s %s\n" % (self.log_date_time_string(), fmt % args))


LANDING = """<!doctype html><meta charset=utf-8><title>Night Sky for TRMNL</title>
<body style="font:16px/1.5 system-ui;max-width:40em;margin:3em auto;padding:0 1em">
<h1>Night Sky &#9733;</h1>
<p>A live star chart + stargazing forecast backend for <a href="https://trmnl.com">TRMNL</a> e-ink displays.</p>
<p>Try <a href="/sky?loc=51.48,0&label=Greenwich">/sky?loc=51.48,0&amp;label=Greenwich</a></p>
</body>"""


def main():
    port = int(os.environ.get("PORT", "8080"))
    host = os.environ.get("HOST", "0.0.0.0")
    srv = ThreadingHTTPServer((host, port), Handler)
    print(f"Night Sky backend on http://{host}:{port}  (try /sky?loc=51.48,0)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
