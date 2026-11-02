#!/usr/bin/env python3
"""Build backend/data/catalog.json from the d3-celestial data files.

Source data: https://github.com/ofrohn/d3-celestial (BSD-3-Clause, (c) Olaf Frohn)
  stars.6.json, starnames.json, constellations.json,
  constellations.lines.json, mw.json

Usage:
  python3 tools/build_catalog.py <dir-with-d3-celestial-json> [backend/data/catalog.json]

The output is small (~120 KB), so the server can load it at startup.
"""
import json
import math
import sys
from pathlib import Path

MAG_LIMIT = 5.3        # faintest star kept; ~1,500 stars, ~half above the horizon at any time
NAME_MAG_LIMIT = 1.6   # stars bright enough to get a name label
MW_STEP_DEG = 1.6      # Milky Way outline simplification
MW_LEVELS = ("ol1", "ol3")  # faint outer glow + brighter core


def norm_ra(ra):
    return ra % 360.0


def r2(x):
    return round(x, 2)


def ang(a, b):
    """Angular distance in degrees between two [ra, dec] points."""
    ra1, d1, ra2, d2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    c = math.sin(d1) * math.sin(d2) + math.cos(d1) * math.cos(d2) * math.cos(ra1 - ra2)
    return math.degrees(math.acos(max(-1.0, min(1.0, c))))


def simplify(ring, step):
    out = [ring[0]]
    for p in ring[1:]:
        if ang(out[-1], p) >= step:
            out.append(p)
    return out


def main():
    src = Path(sys.argv[1])
    dst = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(__file__).resolve().parent.parent / "backend/data/catalog.json"

    stars_raw = json.loads((src / "stars.6.json").read_text())
    names = json.loads((src / "starnames.json").read_text())
    cons = json.loads((src / "constellations.json").read_text())
    lines = json.loads((src / "constellations.lines.json").read_text())
    mw = json.loads((src / "mw.json").read_text())

    stars = []
    for f in stars_raw["features"]:
        mag = f["properties"]["mag"]
        if mag > MAG_LIMIT:
            continue
        ra, dec = f["geometry"]["coordinates"]
        row = [r2(norm_ra(ra)), r2(dec), r2(mag)]
        if mag <= NAME_MAG_LIMIT:
            nm = names.get(str(f["id"]), {}).get("name", "")
            if nm:
                row.append(nm)
        stars.append(row)
    stars.sort(key=lambda s: s[2])

    # Constellation stick figures + label anchor (mean of the figure's vertices)
    by_id = {f["id"]: f for f in cons["features"]}
    con_lines = []
    con_labels = []
    for f in lines["features"]:
        cid = f["id"]
        polys = [[[r2(norm_ra(p[0])), r2(p[1])] for p in pl] for pl in f["geometry"]["coordinates"]]
        con_lines.extend(polys)
        x = y = z = 0.0
        for pl in polys:
            for ra, dec in pl:
                ra, dec = math.radians(ra), math.radians(dec)
                x += math.cos(dec) * math.cos(ra)
                y += math.cos(dec) * math.sin(ra)
                z += math.sin(dec)
        meta = by_id.get(cid, {}).get("properties", {})
        con_labels.append([
            meta.get("name", cid),
            r2(math.degrees(math.atan2(y, x)) % 360),
            r2(math.degrees(math.atan2(z, math.hypot(x, y)))),
            int(meta.get("rank", f["properties"].get("rank", 3))),
        ])

    milky = {}
    for f in mw["features"]:
        if f["id"] not in MW_LEVELS:
            continue
        rings = []
        for poly in f["geometry"]["coordinates"]:
            for ring in poly:
                pts = simplify([[norm_ra(p[0]), p[1]] for p in ring], MW_STEP_DEG)
                if len(pts) >= 5:
                    rings.append([[r2(p[0]), r2(p[1])] for p in pts])
        milky[f["id"]] = rings

    out = {
        "attribution": "Star, constellation and Milky Way data: d3-celestial (c) Olaf Frohn, BSD-3-Clause",
        "stars": stars,
        "lines": con_lines,
        "constellations": con_labels,
        "milky_way": milky,
    }
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(json.dumps(out, separators=(",", ":"), ensure_ascii=False))
    print(f"wrote {dst}: {len(stars)} stars, {len(con_lines)} figure lines, "
          f"{len(con_labels)} constellations, "
          f"{sum(len(r) for v in milky.values() for r in v)} Milky Way points, "
          f"{dst.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
