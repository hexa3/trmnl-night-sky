"""Run:  python3 -m unittest discover tests   (PyEphem cross-checks run if `ephem` is installed)"""
import json
import math
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
import astro  # noqa: E402
import server  # noqa: E402
import sky  # noqa: E402

try:
    import ephem
except ImportError:
    ephem = None

T = datetime(2026, 11, 1, 16, 30, tzinfo=timezone.utc)


class Astro(unittest.TestCase):
    def test_solstice_sun_declination(self):
        jd = astro.julian_day(datetime(2026, 6, 21, 8, 24, tzinfo=timezone.utc))
        self.assertAlmostEqual(astro.sun_position(jd)["dec"], 23.44, delta=0.02)

    def test_polaris_altitude_equals_latitude(self):
        s = sky.Sky(astro.julian_day(T), 51.48, 0)
        alt, _ = s.altaz_j2000(37.95, 89.26)
        self.assertAlmostEqual(alt, 51.48, delta=0.8)

    def test_projection(self):
        self.assertEqual(tuple(map(round, sky.project(90, 0))), (500, 500))
        x, y = sky.project(0, 0)       # north horizon -> top
        self.assertAlmostEqual(y, 20, delta=0.01)
        x, y = sky.project(0, 90)      # east horizon -> left (looking up)
        self.assertAlmostEqual(x, 20, delta=0.01)

    @unittest.skipIf(ephem is None, "PyEphem not installed")
    def test_against_pyephem(self):
        for dt in (T, datetime(2019, 1, 21, 5, tzinfo=timezone.utc), datetime(2031, 7, 4, 22, tzinfo=timezone.utc)):
            o = ephem.Observer()
            o.lat, o.lon, o.pressure = "-33.87", "151.21", 0
            o.date = dt.strftime("%Y/%m/%d %H:%M:%S")
            jd = astro.julian_day(dt)
            for name in ["sun", "moon"] + astro.PLANETS:
                b = getattr(ephem, name.capitalize())(o)
                alt, az = astro.body_altaz(name, jd, -33.87, 151.21)
                err = math.degrees(ephem.separation((math.radians(az), math.radians(alt)), (b.az, b.alt)))
                self.assertLess(err, 0.15, f"{name} at {dt}: {err:.3f} deg")
            self.assertAlmostEqual(astro.moon_phase(jd)["illum"] * 100, ephem.Moon(o).phase, delta=1)


class Payload(unittest.TestCase):
    def test_build_shape_and_size(self):
        d = sky.build(31.52, 74.36, "Asia/Karachi", when=T, label="Lahore")
        for k in ("meta", "score", "sun", "moon", "planets", "chart", "highlights"):
            self.assertIn(k, d)
        self.assertEqual(len(d["chart"]["stars"]), 6)
        self.assertTrue(0 <= d["score"]["value"] <= 10)
        self.assertEqual(d["sun"]["set"], "17:13")
        size = len(json.dumps(d, separators=(",", ":")))
        self.assertLess(size, 40_000, "payload should stay small for TRMNL polling")

    def test_daytime_auto_shows_tonight(self):
        d = sky.build(31.52, 74.36, "Asia/Karachi", when=datetime(2026, 11, 1, 6, tzinfo=timezone.utc))
        self.assertEqual(d["meta"]["chart_label"], "tonight")
        d = sky.build(31.52, 74.36, "Asia/Karachi", when=datetime(2026, 11, 1, 6, tzinfo=timezone.utc), mode="now")
        self.assertEqual(d["meta"]["chart_label"], "now")

    def test_morning_talks_about_coming_night(self):
        # 10:00 local in Lahore: sunset should be this evening's (17:13 on Nov 1)
        d = sky.build(31.52, 74.36, "Asia/Karachi", when=datetime(2026, 11, 1, 5, tzinfo=timezone.utc))
        self.assertEqual(d["sun"]["set"], "17:13")
        # 03:00 local: still inside the night that began Oct 31
        d = sky.build(31.52, 74.36, "Asia/Karachi", when=datetime(2026, 10, 31, 22, tzinfo=timezone.utc))
        self.assertEqual(d["sun"]["set"], "17:14")

    def test_polar_night_and_day(self):
        d = sky.build(78.2, 15.6, "Arctic/Longyearbyen", when=datetime(2026, 12, 21, 12, tzinfo=timezone.utc))
        self.assertEqual(d["sun"]["polar"], "night")
        d = sky.build(78.2, 15.6, "Arctic/Longyearbyen", when=datetime(2026, 6, 21, 12, tzinfo=timezone.utc))
        self.assertEqual(d["sun"]["polar"], "day")
        self.assertEqual(d["score"]["value"], 0)

    def test_labels_do_not_overlap_markers(self):
        d = sky.build(-33.87, 151.21, "Australia/Sydney", when=datetime(2026, 11, 7, 10, tzinfo=timezone.utc))
        names = [t["text"] for t in d["chart"]["tags"]]
        self.assertEqual(len(names), len(set(names)))

    def test_server_params(self):
        d = server.handle_sky({"loc": "51.48,-0.0015", "tz": "Europe/London", "clouds": "no", "t": "2026-11-01T22:00:00Z"})
        self.assertEqual(d["meta"]["tz"], "Europe/London")
        with self.assertRaises((ValueError, KeyError)):
            server.handle_sky({"loc": "999,0", "clouds": "no", "tz": "UTC"})


if __name__ == "__main__":
    unittest.main()
