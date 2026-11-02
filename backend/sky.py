"""Build the JSON payload the TRMNL plugin renders.

Geometry is pre-projected into a 1000x1000 SVG viewBox (zenith at the centre,
horizon circle radius HORIZON_R, north up, east LEFT - the way the sky looks
when you lie on your back with your head to the north). Shapes are sent as
compact SVG path strings so the Liquid templates stay tiny and the payload
stays small (~15-25 KB).
"""
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import astro
from astro import cosd, sind

CATALOG = json.loads((Path(__file__).parent / "data" / "catalog.json").read_text())

C = 500            # viewBox centre
HORIZON_R = 480    # horizon radius in viewBox units
MAX_ZENITH = 150   # clamp below-horizon points (SVG clips them anyway)

# Star size buckets by magnitude (templates choose a pixel width per bucket)
MAG_BUCKETS = [0.5, 1.5, 2.5, 3.5, 4.5, 99]

PLANET_SYMBOLS = {"Mercury": "☿", "Venus": "♀", "Mars": "♂", "Jupiter": "♃",
                  "Saturn": "♄", "Uranus": "⛢", "Neptune": "♆"}

# (name, peak month, peak day, start (m, d), end (m, d), ZHR) - IMO calendar
METEOR_SHOWERS = [
    ("Quadrantids", 1, 3, (12, 28), (1, 12), 110),
    ("Lyrids", 4, 22, (4, 14), (4, 30), 18),
    ("Eta Aquariids", 5, 6, (4, 19), (5, 28), 50),
    ("S. Delta Aquariids", 7, 30, (7, 12), (8, 23), 25),
    ("Perseids", 8, 12, (7, 17), (8, 24), 100),
    ("Draconids", 10, 8, (10, 6), (10, 10), 10),
    ("Orionids", 10, 21, (10, 2), (11, 7), 20),
    ("S. Taurids", 11, 5, (9, 10), (11, 20), 5),
    ("N. Taurids", 11, 12, (10, 20), (12, 10), 5),
    ("Leonids", 11, 17, (11, 6), (11, 30), 15),
    ("Geminids", 12, 14, (12, 4), (12, 20), 150),
    ("Ursids", 12, 22, (12, 17), (12, 26), 10),
]


# ------------------------------------------------------------ projection

def project(alt, az):
    """Stereographic projection centred on the zenith. Returns (x, y)."""
    z = min(90.0 - alt, MAX_ZENITH)
    r = HORIZON_R * math.tan(math.radians(z) / 2)   # tan(45 deg) == 1 at horizon
    return C - r * sind(az), C - r * cosd(az)


class Sky:
    """Observer at a given place + time; projects RA/Dec to chart coordinates."""

    def __init__(self, jd, lat, lon):
        self.jd, self.lat, self.lon = jd, lat, lon
        self.lst = astro.local_sidereal(jd, lon)
        self.pm = astro.precess_matrix(jd)
        self.sl, self.cl = sind(lat), cosd(lat)

    def altaz_j2000(self, ra, dec):
        ra, dec = astro.precess(ra, dec, self.pm)
        return astro.equatorial_to_horizontal(ra, dec, self.lst, self.lat)

    def altaz(self, ra, dec):
        return astro.equatorial_to_horizontal(ra, dec, self.lst, self.lat)


def fmt(v):
    return str(int(round(v)))


def polyline_path(points, min_alt=-25.0):
    """points: [(alt, x, y)] -> 'M..L..' string, broken where it dips far below the horizon."""
    out, pen = [], False
    for alt, x, y in points:
        if alt < min_alt:
            pen = False
            continue
        out.append(("L" if pen else "M") + fmt(x) + " " + fmt(y))
        pen = True
    return "".join(out)


def moon_glyph_path(illum, waxing, r=100):
    """Lit part of the moon disc centred at (0,0), radius r, as an SVG path."""
    k = illum
    rx = abs(2 * k - 1) * r
    # right half-disc (lit limb for waxing), then back along the terminator ellipse
    sweep = 0 if k < 0.5 else 1
    d = f"M0 {-r}A{r} {r} 0 0 1 0 {r}A{rx:.1f} {r} 0 0 {sweep} 0 {-r}Z"
    return d, (1 if waxing else -1)


# ---------------------------------------------------------------- timing

def local_time(jd, tz):
    return datetime.fromtimestamp((jd - 2440587.5) * 86400.0, tz)


def hhmm(jd, tz, clock24=True):
    if jd is None:
        return None
    t = local_time(jd, tz)
    if clock24:
        return t.strftime("%H:%M")
    return t.strftime("%-I:%M%p").lower().replace("m", "")


def sample(body, jd0, hours, step_min, lat, lon):
    n = int(hours * 60 / step_min) + 1
    out = []
    for i in range(n):
        jd = jd0 + i * step_min / 1440.0
        alt, az = astro.body_altaz(body, jd, lat, lon)
        out.append((jd, alt, az))
    return out


def night_window(now_jd, tz, lat, lon):
    """JD of the local noon that starts 'tonight'.

    Afternoon/evening -> today's noon. After midnight while it's still dark ->
    yesterday's noon (we're inside last night). Morning daylight -> today's
    noon, i.e. talk about the coming night.
    """
    now = local_time(now_jd, tz)
    noon = now.replace(hour=12, minute=0, second=0, microsecond=0)
    if now.hour < 12 and astro.body_altaz("sun", now_jd, lat, lon)[0] < -0.833:
        noon -= timedelta(days=1)
    return astro.julian_day(noon)


def first(events, kind):
    for jd, k in events:
        if k == kind:
            return jd
    return None


# ------------------------------------------------------------- payload

def build(lat, lon, tz_name="UTC", when=None, mode="auto", label="", clock24=True,
          mag_limit=5.0, clouds=None):
    tz = ZoneInfo(tz_name)
    now = when or datetime.now(timezone.utc)
    now_jd = astro.julian_day(now)

    # ---- tonight's sun + moon timeline (local noon -> next local noon, 5 min steps)
    noon_jd = night_window(now_jd, tz, lat, lon)
    sun_s = sample("sun", noon_jd, 24, 5, lat, lon)
    moon_s = sample("moon", noon_jd, 24, 5, lat, lon)
    sun_alt = [(j, a) for j, a, _ in sun_s]
    ev_set = astro.find_crossings(sun_alt, -0.833)
    ev_civil = astro.find_crossings(sun_alt, -6)
    ev_astro = astro.find_crossings(sun_alt, -18)
    # moon altitude is already topocentric, so use refraction + semidiameter only
    ev_moon = astro.find_crossings([(j, a) for j, a, _ in moon_s], -0.833)

    sunset, sunrise = first(ev_set, "set"), first(ev_set, "rise")
    dusk, dawn = first(ev_astro, "set"), first(ev_astro, "rise")
    max_sun = max(a for _, a, _ in sun_s)
    min_sun = min(a for _, a, _ in sun_s)

    # dark samples: sun below -18 (fall back to -12 at high latitudes in summer)
    dark_limit = -18 if min_sun < -18 else -12
    dark_idx = [i for i, (_, a, _) in enumerate(sun_s) if a < dark_limit]
    dark_hours = len(dark_idx) * 5 / 60.0

    # ---- moon
    phase = astro.moon_phase(now_jd)
    moon_free = [i for i in dark_idx if moon_s[i][1] < 0]
    # quality: each dark sample scores 1 if moon down, else reduced by moon brightness
    if dark_idx:
        q = sum(1.0 if moon_s[i][1] < 0 else 1.0 - 0.85 * phase["illum"] * min(1, math.sqrt(max(0, sind(moon_s[i][1])) + 0.25))
                for i in dark_idx) / len(dark_idx)
    else:
        q = 0.0
    hours_factor = min(1.0, dark_hours / 6.0) if dark_idx else 0.0
    score = q * (0.6 + 0.4 * hours_factor)
    cloud_pct = None
    if clouds:
        dark_jds = [sun_s[i][0] for i in dark_idx] or [now_jd]
        vals = [c for jd, c in clouds if dark_jds[0] - 0.02 <= jd <= dark_jds[-1] + 0.02]
        if vals:
            cloud_pct = sum(vals) / len(vals)
            score *= (1 - cloud_pct / 100.0) ** 1.2
    score10 = max(0, min(10, round(score * 10)))
    verdict = ["Stay in", "Stay in", "Poor", "Poor", "Fair", "Fair", "Decent", "Good", "Great", "Excellent", "Perfect"][score10]

    moon_free_span = None
    if moon_free:
        # longest contiguous moon-free dark run
        runs, cur = [], [moon_free[0]]
        for i in moon_free[1:]:
            if i == cur[-1] + 1:
                cur.append(i)
            else:
                runs.append(cur)
                cur = [i]
        runs.append(cur)
        best = max(runs, key=len)
        moon_free_span = (sun_s[best[0]][0], sun_s[best[-1]][0])

    # ---- which instant does the chart show?
    sun_now = astro.body_altaz("sun", now_jd, lat, lon)[0]
    chart_jd, chart_label = now_jd, "now"
    if mode == "auto" and sun_now > -6:
        # it's light out: show the sky an hour after the next civil dusk
        ahead = [(j, a) for j, a, _ in sample("sun", now_jd, 24, 10, lat, lon)]
        target = first(astro.find_crossings(ahead, -6), "set")
        if target:
            chart_jd = target + 1 / 24.0
            chart_label = "tonight"
    sky = Sky(chart_jd, lat, lon)

    # ---- stars (bucketed by magnitude into dot paths)
    buckets = [[] for _ in MAG_BUCKETS]
    named = []
    for row in CATALOG["stars"]:
        ra, dec, mag = row[0], row[1], row[2]
        if mag > mag_limit:
            break
        alt, az = sky.altaz_j2000(ra, dec)
        if alt < 0:
            continue
        x, y = project(alt, az)
        b = next(i for i, lim in enumerate(MAG_BUCKETS) if mag < lim)
        buckets[b].append(f"M{fmt(x)} {fmt(y)}h0")
        if len(row) > 3 and alt > 8:
            named.append({"name": row[3], "x": round(x), "y": round(y), "mag": mag})
    star_paths = ["".join(b) for b in buckets]
    named = named[:12]

    # ---- constellation figures
    line_parts = []
    for pl in CATALOG["lines"]:
        pts = []
        for ra, dec in pl:
            alt, az = sky.altaz_j2000(ra, dec)
            x, y = project(alt, az)
            pts.append((alt, x, y))
        if max(p[0] for p in pts) < 0:
            continue
        line_parts.append(polyline_path(pts))
    labels = []
    for name, ra, dec, rank in CATALOG["constellations"]:
        if rank > 1:
            continue
        alt, az = sky.altaz_j2000(ra, dec)
        if alt < 18:
            continue
        x, y = project(alt, az)
        labels.append({"name": name.upper(), "x": round(x), "y": round(y)})

    # ---- Milky Way (two brightness levels, rendered with a dither pattern)
    mw = {}
    for level, rings in CATALOG["milky_way"].items():
        d = []
        for ring in rings:
            pts = []
            for ra, dec in ring:
                alt, az = sky.altaz_j2000(ra, dec)
                x, y = project(alt, az)
                pts.append(f"{fmt(x)} {fmt(y)}")
            d.append("M" + "L".join(pts) + "Z")
        mw[level] = "".join(d)

    # ---- ecliptic
    ecl = []
    eps = 23.4393
    for i in range(0, 361, 3):
        ra, dec = astro.ecliptic_to_equatorial(i, 0, eps)
        alt, az = sky.altaz_j2000(ra, dec)
        x, y = project(alt, az)
        ecl.append((alt, x, y))
    ecliptic = polyline_path(ecl, min_alt=-40)

    # ---- sun / moon on chart
    s_alt, s_az = astro.body_altaz("sun", chart_jd, lat, lon)
    m_alt, m_az = astro.body_altaz("moon", chart_jd, lat, lon)
    chart_phase = astro.moon_phase(chart_jd)
    glyph, flip = moon_glyph_path(chart_phase["illum"], chart_phase["waxing"])
    if lat < 0:
        flip = -flip  # southern hemisphere sees the moon "upside down"
    mx, my = project(max(m_alt, 0), m_az)
    sx, sy = project(max(s_alt, 0), s_az)

    # ---- planets: position on chart + best viewing time tonight
    dark_jds = [sun_s[i][0] for i in dark_idx[::3]]  # every 15 min
    if not dark_jds:
        dark_jds = [sun_s[i][0] for i, (_, a, _) in enumerate(sun_s) if a < -6][::3]
    clock_ = lambda jd: hhmm(jd, tz, clock24)
    planets = []
    moon_now = astro.moon_position(chart_jd)
    for name in astro.PLANETS:
        p = astro.planet_position(name, chart_jd)
        alt, az = sky.altaz(p["ra"], p["dec"])
        x, y = project(alt, az)
        best_alt, best_jd, best_az = -90, None, 0
        for j in dark_jds:
            a, z = astro.body_altaz(name, j, lat, lon)
            if a > best_alt:
                best_alt, best_jd, best_az = a, j, z
        # rise time tonight (coarse 10-min scan of the whole window)
        p_s = [(j, astro.body_altaz(name, j, lat, lon)[0]) for j in
               (noon_jd + k / 144.0 for k in range(0, 145))]
        p_rise = first(astro.find_crossings(p_s, 0), "rise")
        p_set = first(astro.find_crossings(p_s, 0), "set")
        sun_sep = astro.separation(p["ra"], p["dec"], *(lambda s: (s["ra"], s["dec"]))(astro.sun_position(chart_jd)))
        naked_eye = p["mag"] < 6.0
        visible_tonight = best_alt > 8 and p["mag"] < 5.0 and sun_sep > 12
        planets.append({
            "name": name,
            "symbol": PLANET_SYMBOLS[name],
            "mag": round(p["mag"], 1),
            "alt": round(alt),
            "az": round(az),
            "dir": astro.compass8(az),
            "up": alt > 0,
            "x": round(x), "y": round(y),
            "naked_eye": naked_eye,
            "visible_tonight": visible_tonight,
            "best_time": hhmm(best_jd, tz, clock24) if visible_tonight else None,
            "best_alt": round(best_alt),
            "best_dir": astro.compass8(best_az),
            "rise": clock_(p_rise),
            "set": clock_(p_set),
            # one short line for the planet table
            "when": None,
            "_ra": p["ra"], "_dec": p["dec"],
        })

    dusk_jd = dark_jds[0] if dark_jds else now_jd
    for p in planets:
        if not p["visible_tonight"]:
            continue
        a_dusk = astro.body_altaz(p["name"], dusk_jd, lat, lon)[0]
        if a_dusk > 5 or not p["rise"]:
            p["when"] = f"best {p['best_time']} · {p['best_alt']}° {p['best_dir']}"
        else:
            p["when"] = f"rises {p['rise']} · {p['best_alt']}° {p['best_dir']} by {p['best_time']}"

    # ---- highlights
    highlights = []
    for p in planets:
        sep = astro.separation(p["_ra"], p["_dec"], moon_now["ra"], moon_now["dec"])
        if sep < 7 and p["visible_tonight"]:
            highlights.append({"icon": "conj", "text": f"Moon {sep:.0f}° from {p['name']}", "rank": sep})
    vis = [p for p in planets if p["visible_tonight"]]
    for i, a in enumerate(vis):
        for b in vis[i + 1:]:
            sep = astro.separation(a["_ra"], a["_dec"], b["_ra"], b["_dec"])
            if sep < 4:
                highlights.append({"icon": "conj", "text": f"{a['name']} & {b['name']} {sep:.1f}° apart", "rank": sep})
    local_now = local_time(now_jd, tz)
    for name, pm_, pd, start, end, zhr in METEOR_SHOWERS:
        peak = _nearest_date(local_now, pm_, pd)
        days = (peak.date() - local_now.date()).days
        if _in_range(local_now, start, end) or 0 <= days <= 7:
            if days == 0:
                txt = f"{name} peak tonight · ~{zhr}/hr"
            elif days > 0:
                txt = f"{name} peak in {days}d · ~{zhr}/hr"
            else:
                txt = f"{name} active · peaked {-days}d ago"
            highlights.append({"icon": "meteor", "text": txt, "rank": abs(days) + (0 if zhr >= 50 else 10)})
    nm = _next_phase(now_jd)
    if nm:
        highlights.append(nm)
    highlights.sort(key=lambda h: h["rank"])
    for h in highlights:
        h.pop("rank", None)
    for p in planets:
        p.pop("_ra"), p.pop("_dec")

    # moon rise/set within this night window
    moonrise, moonset = first(ev_moon, "rise"), first(ev_moon, "set")
    m_alt_now = astro.body_altaz("moon", now_jd, lat, lon)[0]

    clock = lambda jd: hhmm(jd, tz, clock24)
    visible = [p for p in planets if p["visible_tonight"]]
    visible.sort(key=lambda p: p["mag"])
    tags = place_tags(chart_moon=(mx, my, m_alt > 0),
                      planets=[p for p in planets if p["up"] and p["naked_eye"]],
                      named=named, constellations=labels)

    return {
        "meta": {
            "label": label or _fmt_coords(lat, lon),
            "lat": round(lat, 3), "lon": round(lon, 3),
            "tz": tz_name,
            "generated": local_time(now_jd, tz).isoformat(timespec="minutes"),
            "date": local_time(now_jd, tz).strftime("%a %-d %b"),
            "chart_time": clock(chart_jd),
            "chart_label": chart_label,
            "is_day": sun_now > -0.833,
            "attribution": "Data: d3-celestial (BSD), Open-Meteo",
        },
        "score": {
            "value": score10,
            "verdict": verdict,
            "clouds": None if cloud_pct is None else round(cloud_pct),
            "dark_hours": round(dark_hours, 1),
            "dark_h": int(dark_hours), "dark_m": int(round((dark_hours % 1) * 60)) % 60,
            "moon_free": None if not moon_free_span else f"{clock(moon_free_span[0])}–{clock(moon_free_span[1])}",
            "moon_free_hours": round(len(moon_free) * 5 / 60.0, 1),
            "bar": "".join("■" if i < score10 else "□" for i in range(10)),
        },
        "sun": {
            "set": clock(sunset), "rise": clock(sunrise),
            "dusk": clock(dusk), "dawn": clock(dawn),
            "polar": "day" if min_sun > -0.833 else ("night" if max_sun < -0.833 else None),
            "alt": round(sun_now),
        },
        "moon": {
            "phase": phase["name"],
            "illum": round(phase["illum"] * 100),
            "age": round(phase["age"], 1),
            "waxing": phase["waxing"],
            "rise": clock(moonrise), "set": clock(moonset),
            "up": m_alt_now > 0,
            "alt": round(m_alt_now),
            "glyph": glyph, "flip": flip,
        },
        "planets": planets,
        "visible": visible,
        "visible_count": len(visible),
        "highlights": highlights[:4],
        "chart": {
            "stars": star_paths,
            "lines": "".join(line_parts),
            "milky_faint": mw.get("ol1", ""),
            "milky_bright": mw.get("ol3", ""),
            "ecliptic": ecliptic,
            "tags": tags,
            "moon": {"x": round(mx), "y": round(my), "up": m_alt > 0, "glyph": glyph, "flip": flip},
            "sun": {"x": round(sx), "y": round(sy), "up": s_alt > 0},
            "planets": [p for p in planets if p["up"] and p["naked_eye"]],
        },
    }


def place_tags(chart_moon, planets, named, constellations):
    """Greedy label placement in viewBox units, highest priority first.

    kinds: moon, planet, star, con. Labels for points sit to the right of the
    dot; constellation names are centred. Overlapping lower-priority labels
    and anything spilling outside the horizon circle are dropped.
    """
    # approx glyph width / line height in viewBox units for a ~400px chart
    # (TRMNL12 pixel font ~7px/char -> 17.5 units, bold TRMNL16 ~9px -> 23 units)
    CH = {"con": 19, "star": 18, "planet": 23, "moon": 23}
    H = 40
    placed, boxes = [], []
    # keep the compass letters clear
    for cx, cy in ((C, 40), (C, 960), (40, C), (960, C)):
        boxes.append((cx - 30, cy - 30, cx + 30, cy + 30))

    def box(x, y, text, centred, kind):
        w = len(text) * CH[kind] + 12
        if centred:
            return (x - w / 2, y - H / 2, x + w / 2, y + H / 2)
        return (x + 16, y - H / 2, x + 16 + w, y + H / 2)

    def free(b):
        for o in boxes:
            if b[0] < o[2] and b[2] > o[0] and b[1] < o[3] and b[3] > o[1]:
                return False
        # keep inside the horizon circle
        for cx, cy in ((b[0], b[1]), (b[2], b[1]), (b[0], b[3]), (b[2], b[3])):
            if math.hypot(cx - C, cy - C) > HORIZON_R - 6:
                return False
        return True

    def add(kind, text, x, y, centred=False):
        b = box(x, y, text, centred, kind)
        if centred:
            cands = [(b, "c")]
        else:
            w = b[2] - b[0]
            cands = [
                (b, "r"),
                ((x - 16 - w, b[1], x - 16, b[3]), "l"),
                ((x - w / 2, y - 14 - H, x + w / 2, y - 14), "t"),
                ((x - w / 2, y + 14, x + w / 2, y + 14 + H), "b"),
            ]
        for cb, side in cands:
            if free(cb):
                boxes.append(cb)
                placed.append({"kind": kind, "text": text, "x": round(x / 10, 1), "y": round(y / 10, 1), "side": side})
                return True
        return False

    # reserve every marker first so labels never cover a planet or the moon
    mx, my, mup = chart_moon
    if mup:
        boxes.append((mx - 30, my - 30, mx + 30, my + 30))
    for p in planets:
        boxes.append((p["x"] - 10, p["y"] - 10, p["x"] + 10, p["y"] + 10))
    for p in sorted(planets, key=lambda p: p["mag"]):
        if not add("planet", p["name"], p["x"], p["y"]):
            add("planet", p["name"][:3], p["x"], p["y"])
    if mup:
        add("moon", "Moon", mx + 18, my)
    for st in named:
        add("star", st["name"], st["x"], st["y"])
    for c in constellations:
        add("con", c["name"], c["x"], c["y"], centred=True)
    return placed


def _fmt_coords(lat, lon):
    return f"{abs(lat):.1f}°{'N' if lat >= 0 else 'S'} {abs(lon):.1f}°{'E' if lon >= 0 else 'W'}"


def _nearest_date(now, m, d):
    cands = [now.replace(year=now.year + k, month=m, day=d, hour=0, minute=0, second=0, microsecond=0) for k in (-1, 0, 1)]
    return min(cands, key=lambda c: abs((c - now).total_seconds()))


def _in_range(now, start, end):
    md = (now.month, now.day)
    if start <= end:
        return start <= md <= end
    return md >= start or md <= end


def _next_phase(now_jd):
    """Full or new moon within the next 3 days (searched in 15-minute steps)."""
    prev = astro.moon_phase(now_jd)["elong"]
    for q in range(1, 72 * 4 + 1):
        jd = now_jd + q / 96.0
        e = astro.moon_phase(jd)["elong"]
        name = "Full Moon" if prev < 180 <= e else ("New Moon" if prev > 300 and e < 60 else None)
        if name:
            days = jd - now_jd
            when = "today" if days < 1 else ("tomorrow" if days < 2 else "in 2 days")
            return {"icon": "moon", "text": f"{name} {when}", "rank": 1 + days}
        prev = e
    return None
