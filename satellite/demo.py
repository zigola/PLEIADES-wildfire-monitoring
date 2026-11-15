"""Synthetic MODIS/VIIRS-like hotspots for demos and tests.

THIS IS NOT REAL FIRE DATA. It exists so the site works before real NASA FIRMS
files are imported, and every row it produces is flagged ``is_demo``.

It deliberately reproduces the harmonization problem: VIIRS starts in 2012, sees
smaller fires and reports several pixels per fire, so raw VIIRS counts are far
higher than MODIS counts for the same burning.
"""

from __future__ import annotations

import math
import random
from datetime import date, timedelta

MODIS_START = date(2005, 1, 1)
VIIRS_START = date(2012, 1, 20)
DEFAULT_END = date(2025, 12, 31)
AOI = (20.84, -17.06, 35.74, -1.54)  # south, west, north, east covered by the synthetic generator

# name, lat, lon, spread(deg), weight, regime
REGIONS = [
    ("Rif / Chefchaouen", 35.10, -5.20, 0.35, 3.0, "forest"),
    ("Larache / Ouezzane", 34.90, -5.90, 0.30, 2.0, "forest"),
    ("Gharb plain", 34.20, -6.00, 0.30, 2.5, "agri"),
    ("Saiss / Meknes", 34.00, -5.50, 0.30, 1.5, "agri"),
    ("Middle Atlas", 33.40, -5.20, 0.35, 1.5, "forest"),
    ("Tadla / Beni Mellal", 32.45, -6.35, 0.30, 2.0, "agri"),
    ("High Atlas", 31.90, -6.60, 0.40, 1.0, "mixed"),
    ("Doukkala", 32.80, -8.60, 0.30, 1.2, "agri"),
    ("Souss", 30.40, -9.20, 0.40, 0.8, "mixed"),
    ("Oriental / Berkane", 34.90, -2.30, 0.30, 1.0, "mixed"),
]


def _gauss(x, mu, sigma):
    return math.exp(-0.5 * ((x - mu) / sigma) ** 2)


def seasonal(doy: int, regime: str) -> float:
    forest = 0.04 + _gauss(doy, 215, 32)
    agri = 0.03 + 0.7 * _gauss(doy, 172, 22) + 0.35 * _gauss(doy, 292, 14)
    if regime == "forest":
        return forest
    if regime == "agri":
        return agri
    return 0.5 * (forest + agri)


def _poisson(rng: random.Random, lam: float) -> int:
    if lam <= 0:
        return 0
    limit, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= limit:
            return k
        k += 1


def generate(seed: int = 7, start_year: int = 2005, end: date = DEFAULT_END, scale: float = 1.0):
    """Yield dict rows ready for Hotspot(**row)."""
    rng = random.Random(seed)
    years = range(start_year, end.year + 1)
    year_factor = {y: math.exp(rng.gauss(0, 0.45)) for y in years}
    # a few clearly unusual seasons so the anomaly detector has something to find
    for y in rng.sample([y for y in years if y > 2008], 3):
        year_factor[y] *= 2.6

    day = date(start_year, 1, 1)
    while day <= end:
        doy = day.timetuple().tm_yday
        for _name, lat0, lon0, spread, weight, regime in REGIONS:
            lam = 0.09 * scale * weight * seasonal(doy, regime) * year_factor[day.year]
            for _ in range(_poisson(rng, lam)):
                yield from _event(rng, day, lat0, lon0, spread, regime, end)
        day += timedelta(days=1)


def _event(rng, day, lat0, lon0, spread, regime, end):
    clat = lat0 + rng.gauss(0, spread * 0.6)
    clon = lon0 + rng.gauss(0, spread * 0.6)
    n_pixels = 1 + int(rng.expovariate(1 / (2.6 if regime == "forest" else 1.6)))
    n_days = 1 + int(rng.random() * (3.2 if regime == "forest" else 1.8))
    for k in range(n_days):
        d = day + timedelta(days=k)
        if d > end:
            break
        decay = 0.75 ** k
        for _ in range(n_pixels):
            if rng.random() > decay:
                continue
            lat = clat + rng.gauss(0, 0.012 * (1 + k * 0.3))
            lon = clon + rng.gauss(0, 0.012 * (1 + k * 0.3))
            frp = max(0.4, rng.lognormvariate(1.3, 0.9))
            daytime = rng.random() < 0.6
            hhmm = (1000 + rng.randrange(0, 360, 5)) if daytime else (100 + rng.randrange(0, 240, 5))
            hhmm = hhmm if hhmm % 100 < 60 else hhmm - 40

            # MODIS: ~1 km pixel, one detection, misses small fires
            if d >= MODIS_START and rng.random() < min(0.9, 0.12 + 0.09 * frp):
                conf = max(5, min(100, int(rng.gauss(55 + 4 * math.log1p(frp) * 5, 20))))
                yield {"sensor": "MODIS", "satellite": rng.choice(["Terra", "Aqua"]),
                       "latitude": round(lat + rng.gauss(0, 0.004), 4),
                       "longitude": round(lon + rng.gauss(0, 0.004), 4),
                       "acq_date": d, "acq_time": hhmm, "frp": round(frp * 2.2, 1),
                       "confidence": "l" if conf < 30 else ("h" if conf >= 80 else "n"),
                       "daynight": "D" if daytime else "N", "is_demo": True,
                       "source": "synthetic demo"}
            # VIIRS: ~375 m, more sensitive, several sub-pixels per fire pixel
            if d >= VIIRS_START and rng.random() < min(0.97, 0.45 + 0.12 * frp):
                for _ in range(1 + int(rng.random() * 3.5)):
                    conf = rng.random()
                    yield {"sensor": "VIIRS", "satellite": rng.choice(["N", "N20", "N21"]),
                           "latitude": round(lat + rng.gauss(0, 0.003), 4),
                           "longitude": round(lon + rng.gauss(0, 0.003), 4),
                           "acq_date": d, "acq_time": hhmm, "frp": round(frp * 0.8, 1),
                           "confidence": "l" if conf < 0.12 else ("h" if conf > 0.8 else "n"),
                           "daynight": "D" if daytime else "N", "is_demo": True,
                           "source": "synthetic demo"}
