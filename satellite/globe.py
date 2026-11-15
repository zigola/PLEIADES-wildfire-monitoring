"""Real per-region numbers for the home-page globe (replaces the former random demo values).

Counts come from NASA FIRMS rows stored in the database (is_demo=False), restricted to the
last 24 hours by acquisition time (UTC). Nothing here is simulated.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .models import Hotspot

HALF_SIZE_DEG = 5.0  # each region is the box centre +/- 5 degrees

GLOBE_REGIONS = [
    {"key": "california", "name": "California, USA", "lat": 37.0, "lon": -120.0},
    {"key": "alberta", "name": "Alberta, Canada", "lat": 56.0, "lon": -115.0},
    {"key": "amazon", "name": "Amazon Basin, Brazil", "lat": -6.0, "lon": -58.0},
    {"key": "morocco", "name": "Rif & Middle Atlas, Morocco", "lat": 34.5, "lon": -5.0},
    {"key": "east_med", "name": "Eastern Mediterranean", "lat": 38.0, "lon": 27.0},
    {"key": "africa", "name": "Central African Savanna", "lat": 7.0, "lon": 22.0},
    {"key": "siberia", "name": "Siberia, Russia", "lat": 60.0, "lon": 110.0},
    {"key": "borneo", "name": "Borneo, Indonesia", "lat": -1.5, "lon": 114.0},
    {"key": "nsw", "name": "New South Wales, Australia", "lat": -32.5, "lon": 149.5},
]


def region_bbox(region):
    """(south, west, north, east) clipped to valid ranges."""
    lat, lon = region["lat"], region["lon"]
    return (max(-90.0, lat - HALF_SIZE_DEG), max(-180.0, lon - HALF_SIZE_DEG),
            min(90.0, lat + HALF_SIZE_DEG), min(180.0, lon + HALF_SIZE_DEG))


def globe_stats(hours=24, now=None):
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(hours=hours)
    out = []
    for region in GLOBE_REGIONS:
        s, w, n, e = region_bbox(region)
        rows = (Hotspot.objects.filter(is_demo=False, exact_dup_of__isnull=True, acq_date__gte=since.date(),
                                       latitude__gte=s, latitude__lte=n, longitude__gte=w, longitude__lte=e)
                .values_list("sensor", "acq_date", "acq_time", "frp"))
        count, frp, modis, viirs, last = 0, 0.0, 0, 0, None
        for sensor, day, hhmm, power in rows:
            when = datetime(day.year, day.month, day.day, hhmm // 100, hhmm % 100, tzinfo=timezone.utc)
            if when < since or when > now + timedelta(minutes=5):
                continue
            count += 1
            frp += power or 0.0
            modis += sensor == "MODIS"
            viirs += sensor == "VIIRS"
            last = when if last is None or when > last else last
        out.append({**region, "bbox": [s, w, n, e], "count": count, "frp_mw": round(frp, 1), "modis": modis,
                    "viirs": viirs, "last_detection_utc": last.isoformat() if last else None})
    return out
