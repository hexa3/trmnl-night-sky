"""Dependency-free positional astronomy, accurate to a few arcminutes.

Good enough for a sky chart and rise/set times to about a minute.
Algorithms:
  * Sun     - Astronomical Almanac low-precision formulae
  * Moon    - Paul Schlyter's method with main perturbation terms
  * Planets - JPL Keplerian elements (E.M. Standish), valid 1800-2050
  * Magnitudes - Explanatory Supplement to the Astronomical Almanac (1992)

Angles are in degrees unless noted. Catalog (J2000) and planet coordinates are precessed to the equator of date.
"""
import math
from datetime import datetime, timezone

D2R = math.pi / 180.0
R2D = 180.0 / math.pi
OBLIQUITY_J2000 = 23.43928


def sind(x): return math.sin(x * D2R)
def cosd(x): return math.cos(x * D2R)
def atan2d(y, x): return math.atan2(y, x) * R2D
def asind(x): return math.asin(max(-1.0, min(1.0, x))) * R2D
def norm360(x): return x % 360.0


def julian_day(dt: datetime) -> float:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp() / 86400.0 + 2440587.5


def gmst(jd: float) -> float:
    t = (jd - 2451545.0) / 36525.0
    return norm360(280.46061837 + 360.98564736629 * (jd - 2451545.0)
                   + 0.000387933 * t * t - t ** 3 / 38710000.0)


def local_sidereal(jd: float, lon: float) -> float:
    return norm360(gmst(jd) + lon)


def ecliptic_to_equatorial(lon, lat, eps=OBLIQUITY_J2000):
    x = cosd(lat) * cosd(lon)
    y = cosd(lat) * sind(lon) * cosd(eps) - sind(lat) * sind(eps)
    z = cosd(lat) * sind(lon) * sind(eps) + sind(lat) * cosd(eps)
    return norm360(atan2d(y, x)), asind(z)


def equatorial_to_horizontal(ra, dec, lst, lat):
    """Return (altitude, azimuth). Azimuth from north through east."""
    h = lst - ra
    sin_alt = sind(lat) * sind(dec) + cosd(lat) * cosd(dec) * cosd(h)
    alt = asind(sin_alt)
    az = atan2d(-sind(h) * cosd(dec), cosd(lat) * sind(dec) - sind(lat) * cosd(dec) * cosd(h))
    return alt, norm360(az)


def separation(ra1, dec1, ra2, dec2):
    c = sind(dec1) * sind(dec2) + cosd(dec1) * cosd(dec2) * cosd(ra1 - ra2)
    return math.acos(max(-1.0, min(1.0, c))) * R2D


def precess_matrix(jd):
    """IAU 1976 precession matrix J2000 -> mean equator of date."""
    t = (jd - 2451545.0) / 36525.0
    asec = D2R / 3600.0
    zeta = (2306.2181 * t + 0.30188 * t * t + 0.017998 * t ** 3) * asec
    z = (2306.2181 * t + 1.09468 * t * t + 0.018203 * t ** 3) * asec
    th = (2004.3109 * t - 0.42665 * t * t - 0.041833 * t ** 3) * asec
    cz, sz, cZ, sZ, ct, st = math.cos(zeta), math.sin(zeta), math.cos(z), math.sin(z), math.cos(th), math.sin(th)
    return (
        (cZ * ct * cz - sZ * sz, -cZ * ct * sz - sZ * cz, -cZ * st),
        (sZ * ct * cz + cZ * sz, -sZ * ct * sz + cZ * cz, -sZ * st),
        (st * cz, -st * sz, ct),
    )


def precess(ra, dec, m):
    """Apply a precession matrix from precess_matrix() to J2000 RA/Dec."""
    x, y, z = cosd(dec) * cosd(ra), cosd(dec) * sind(ra), sind(dec)
    X = m[0][0] * x + m[0][1] * y + m[0][2] * z
    Y = m[1][0] * x + m[1][1] * y + m[1][2] * z
    Z = m[2][0] * x + m[2][1] * y + m[2][2] * z
    return norm360(atan2d(Y, X)), asind(Z)


# --------------------------------------------------------------------- Sun

def sun_position(jd):
    """Return dict with ra, dec, ecliptic lon, distance (AU)."""
    n = jd - 2451545.0
    L = norm360(280.460 + 0.9856474 * n)
    g = norm360(357.528 + 0.9856003 * n)
    lam = L + 1.915 * sind(g) + 0.020 * sind(2 * g)
    eps = 23.439 - 0.0000004 * n
    ra = norm360(atan2d(cosd(eps) * sind(lam), cosd(lam)))
    dec = asind(sind(eps) * sind(lam))
    dist = 1.00014 - 0.01671 * cosd(g) - 0.00014 * cosd(2 * g)
    return {"ra": ra, "dec": dec, "lon": norm360(lam), "dist": dist}


# -------------------------------------------------------------------- Moon

def moon_position(jd):
    """Geocentric Moon: ra, dec, ecliptic lon/lat, distance in Earth radii."""
    d = jd - 2451543.5
    N = norm360(125.1228 - 0.0529538083 * d)
    i = 5.1454
    w = norm360(318.0634 + 0.1643573223 * d)
    a = 60.2666
    e = 0.054900
    M = norm360(115.3654 + 13.0649929509 * d)
    Ms = norm360(356.0470 + 0.9856002585 * d)
    ws = 282.9404 + 4.70935e-5 * d

    E = M + R2D * e * sind(M) * (1 + e * cosd(M))
    for _ in range(6):
        E = E - (E - R2D * e * sind(E) - M) / (1 - e * cosd(E))
    xv = a * (cosd(E) - e)
    yv = a * math.sqrt(1 - e * e) * sind(E)
    v = atan2d(yv, xv)
    r = math.hypot(xv, yv)

    xh = r * (cosd(N) * cosd(v + w) - sind(N) * sind(v + w) * cosd(i))
    yh = r * (sind(N) * cosd(v + w) + cosd(N) * sind(v + w) * cosd(i))
    zh = r * sind(v + w) * sind(i)
    lon = atan2d(yh, xh)
    lat = atan2d(zh, math.hypot(xh, yh))

    Ls = Ms + ws
    Lm = M + w + N
    D = Lm - Ls
    F = Lm - N
    lon += (-1.274 * sind(M - 2 * D) + 0.658 * sind(2 * D) - 0.186 * sind(Ms)
            - 0.059 * sind(2 * M - 2 * D) - 0.057 * sind(M - 2 * D + Ms)
            + 0.053 * sind(M + 2 * D) + 0.046 * sind(2 * D - Ms)
            + 0.041 * sind(M - Ms) - 0.035 * sind(D) - 0.031 * sind(M + Ms)
            - 0.015 * sind(2 * F - 2 * D) + 0.011 * sind(M - 4 * D))
    lat += (-0.173 * sind(F - 2 * D) - 0.055 * sind(M - F - 2 * D)
            - 0.046 * sind(M + F - 2 * D) + 0.033 * sind(F + 2 * D)
            + 0.017 * sind(2 * M + F))
    r += -0.58 * cosd(M - 2 * D) - 0.46 * cosd(2 * D)

    t = (jd - 2451545.0) / 36525.0
    eps = 23.439291 - 0.0130042 * t
    ra, dec = ecliptic_to_equatorial(norm360(lon), lat, eps)
    return {"ra": ra, "dec": dec, "lon": norm360(lon), "lat": lat, "dist": r}


def moon_topocentric_alt(alt, dist_earth_radii):
    """Correct geocentric altitude for lunar parallax (up to ~1 deg)."""
    return alt - asind(math.cos(alt * D2R) / dist_earth_radii)


def moon_phase(jd):
    """Illuminated fraction 0..1, waxing flag, age in days, phase name."""
    s = sun_position(jd)
    m = moon_position(jd)
    elong = norm360(m["lon"] - s["lon"])           # 0 new, 180 full
    illum = (1 - cosd(elong)) / 2
    waxing = elong < 180
    age = elong / 360.0 * 29.530588
    names = ["New Moon", "Waxing Crescent", "First Quarter", "Waxing Gibbous",
             "Full Moon", "Waning Gibbous", "Last Quarter", "Waning Crescent"]
    name = names[int(((elong + 22.5) % 360) // 45)]
    return {"illum": illum, "waxing": waxing, "age": age, "elong": elong, "name": name}


# ----------------------------------------------------------------- Planets

# a, e, I, L, long.peri, long.node  and their rates per Julian century
_ELEMENTS = {
    "Mercury": ([0.38709927, 0.20563593, 7.00497902, 252.25032350, 77.45779628, 48.33076593],
                [0.00000037, 0.00001906, -0.00594749, 149472.67411175, 0.16047689, -0.12534081]),
    "Venus":   ([0.72333566, 0.00677672, 3.39467605, 181.97909950, 131.60246718, 76.67984255],
                [0.00000390, -0.00004107, -0.00078890, 58517.81538729, 0.00268329, -0.27769418]),
    "Earth":   ([1.00000261, 0.01671123, -0.00001531, 100.46457166, 102.93768193, 0.0],
                [0.00000562, -0.00004392, -0.01294668, 35999.37244981, 0.32327364, 0.0]),
    "Mars":    ([1.52371034, 0.09339410, 1.84969142, -4.55343205, -23.94362959, 49.55953891],
                [0.00001847, 0.00007882, -0.00813131, 19140.30268499, 0.44441088, -0.29257343]),
    "Jupiter": ([5.20288700, 0.04838624, 1.30439695, 34.39644051, 14.72847983, 100.47390909],
                [-0.00011607, -0.00013253, -0.00183714, 3034.74612775, 0.21252668, 0.20469106]),
    "Saturn":  ([9.53667594, 0.05386179, 2.48599187, 49.95424423, 92.59887831, 113.66242448],
                [-0.00125060, -0.00050991, 0.00193609, 1222.49362201, -0.41897216, -0.28867794]),
    "Uranus":  ([19.18916464, 0.04725744, 0.77263783, 313.23810451, 170.95427630, 74.01692503],
                [-0.00196176, -0.00004397, -0.00242939, 428.48202785, 0.40805281, 0.04240589]),
    "Neptune": ([30.06992276, 0.00859048, 1.77004347, -55.12002969, 44.96476227, 131.78422574],
                [0.00026291, 0.00005105, 0.00035372, 218.45945325, -0.32241464, -0.00508664]),
}

PLANETS = ["Mercury", "Venus", "Mars", "Jupiter", "Saturn", "Uranus", "Neptune"]


def _heliocentric(name, jd):
    t = (jd - 2451545.0) / 36525.0
    el, rate = _ELEMENTS[name]
    a, e, I, L, wbar, node = (el[k] + rate[k] * t for k in range(6))
    w = wbar - node
    M = (L - wbar + 180.0) % 360.0 - 180.0
    E = M + R2D * e * sind(M)
    for _ in range(8):
        dE = (M - (E - R2D * e * sind(E))) / (1 - e * cosd(E))
        E += dE
        if abs(dE) < 1e-7:
            break
    xp = a * (cosd(E) - e)
    yp = a * math.sqrt(1 - e * e) * sind(E)
    cw, sw, cn, sn, ci, si = cosd(w), sind(w), cosd(node), sind(node), cosd(I), sind(I)
    x = (cw * cn - sw * sn * ci) * xp + (-sw * cn - cw * sn * ci) * yp
    y = (cw * sn + sw * cn * ci) * xp + (-sw * sn + cw * cn * ci) * yp
    z = (sw * si) * xp + (cw * si) * yp
    return x, y, z


_MAG = {
    "Mercury": lambda i: -0.42 + 0.0380 * i - 0.000273 * i * i + 0.000002 * i ** 3,
    "Venus":   lambda i: -4.40 + 0.0009 * i + 0.000239 * i * i - 0.00000065 * i ** 3,
    "Mars":    lambda i: -1.52 + 0.016 * i,
    "Jupiter": lambda i: -9.40 + 0.005 * i,
    "Saturn":  lambda i: -8.88 + 0.044 * i,
    "Uranus":  lambda i: -7.19,
    "Neptune": lambda i: -6.87,
}


def planet_position(name, jd):
    """Geocentric RA/Dec, distance (AU) and visual magnitude."""
    px, py, pz = _heliocentric(name, jd)
    ex, ey, ez = _heliocentric("Earth", jd)
    gx, gy, gz = px - ex, py - ey, pz - ez
    eps = OBLIQUITY_J2000
    xq = gx
    yq = gy * cosd(eps) - gz * sind(eps)
    zq = gy * sind(eps) + gz * cosd(eps)
    delta = math.sqrt(gx * gx + gy * gy + gz * gz)
    r = math.sqrt(px * px + py * py + pz * pz)
    R = math.sqrt(ex * ex + ey * ey + ez * ez)
    cos_i = (r * r + delta * delta - R * R) / (2 * r * delta)
    phase_angle = math.acos(max(-1.0, min(1.0, cos_i))) * R2D
    mag = _MAG[name](phase_angle) + 5 * math.log10(r * delta)
    ra, dec = norm360(atan2d(yq, xq)), asind(zq / delta)
    if name == "Saturn":
        # ring brightening: B = tilt of ring plane seen from Earth
        pole_ra, pole_dec = 40.589, 83.537
        sin_b = abs(sind(dec) * sind(pole_dec) + cosd(dec) * cosd(pole_dec) * cosd(ra - pole_ra))
        mag += -2.60 * sin_b + 1.25 * sin_b * sin_b
    ra, dec = precess(ra, dec, precess_matrix(jd))
    return {
        "ra": ra,
        "dec": dec,
        "dist": delta,
        "mag": mag,
    }


# ------------------------------------------------------------- utilities

def body_altaz(body, jd, lat, lon):
    """Alt/az of 'sun', 'moon' or a planet name at jd (moon is topocentric)."""
    lst = local_sidereal(jd, lon)
    if body == "sun":
        p = sun_position(jd)
        alt, az = equatorial_to_horizontal(p["ra"], p["dec"], lst, lat)
    elif body == "moon":
        p = moon_position(jd)
        alt, az = equatorial_to_horizontal(p["ra"], p["dec"], lst, lat)
        alt = moon_topocentric_alt(alt, p["dist"])
    else:
        p = planet_position(body, jd)
        alt, az = equatorial_to_horizontal(p["ra"], p["dec"], lst, lat)
    return alt, az


def find_crossings(samples, threshold):
    """samples: list of (jd, alt). Return [(jd, 'rise'|'set')] where alt crosses threshold."""
    out = []
    for (j0, a0), (j1, a1) in zip(samples, samples[1:]):
        if (a0 - threshold) * (a1 - threshold) < 0 or (a0 != a1 and a1 == threshold):
            f = (threshold - a0) / (a1 - a0)
            out.append((j0 + f * (j1 - j0), "rise" if a1 > a0 else "set"))
    return out


COMPASS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
           "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]


def compass(az):
    return COMPASS[int((az + 11.25) % 360 // 22.5)]


def compass8(az):
    return ["N", "NE", "E", "SE", "S", "SW", "W", "NW"][int((az + 22.5) % 360 // 45)]
