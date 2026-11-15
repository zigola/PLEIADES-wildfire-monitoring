"""Temporal analysis on real acquisition timestamps (UTC)."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, timedelta
from statistics import median

from django.db.models import Count


def hour_histogram(qs):
    """Acquisition hour (UTC) per sensor and per platform: shows overpass timing differences."""
    sensor_hours = {"MODIS": [0] * 24, "VIIRS": [0] * 24}
    plat = defaultdict(list)
    from satellite.models import Hotspot
    for sensor, sat, t in qs.order_by().values_list("sensor", "satellite", "acq_time").iterator(chunk_size=5000):
        sensor_hours[sensor][min(23, t // 100)] += 1
        plat[Hotspot.PLATFORMS.get(sat.strip().lower(), sat or "unknown")].append((t // 100) * 60 + t % 100)
    platforms = [{"platform": p, "n": len(v), "median_utc": f"{int(median(v)) // 60:02d}:{int(median(v)) % 60:02d}"}
                 for p, v in sorted(plat.items())]
    return {"hours_utc": sensor_hours, "platforms": platforms,
            "note": "All times are UTC (the FIRMS acq_time). Local solar time differs by longitude."}


def aggregate(qs, period="month"):
    """Counts per sensor by day / ISO week / month. Periods without rows are simply absent: use the
    calendar endpoint to tell 'zero' from 'not observed'."""
    out = defaultdict(lambda: {"MODIS": 0, "VIIRS": 0})
    for sensor, day in qs.order_by().values_list("sensor", "acq_date").iterator(chunk_size=5000):
        if period == "day":
            key = day.isoformat()
        elif period == "week":
            iso = day.isocalendar()
            key = f"{iso[0]}-W{iso[1]:02d}"
        else:
            key = day.strftime("%Y-%m")
        out[key][sensor] += 1
    return [{"period": k, "modis": v["MODIS"], "viirs": v["VIIRS"]} for k, v in sorted(out.items())]


def seasonal(qs):
    """Mean detections per calendar month across the years present (only months with data)."""
    per = defaultdict(lambda: {"MODIS": 0, "VIIRS": 0})
    for sensor, day in qs.order_by().values_list("sensor", "acq_date").iterator(chunk_size=5000):
        per[(day.year, day.month)][sensor] += 1
    months = []
    for m in range(1, 13):
        rows = [v for (y, mm), v in per.items() if mm == m]
        months.append({"month": m, "years": len(rows),
                       "modis_mean": round(sum(r["MODIS"] for r in rows) / len(rows), 1) if rows else None,
                       "viirs_mean": round(sum(r["VIIRS"] for r in rows) / len(rows), 1) if rows else None})
    return months


def yearly(qs):
    out = defaultdict(lambda: {"MODIS": 0, "VIIRS": 0})
    for sensor, day in qs.order_by().values_list("sensor", "acq_date").iterator(chunk_size=5000):
        out[day.year][sensor] += 1
    return [{"year": y, "modis": v["MODIS"], "viirs": v["VIIRS"]} for y, v in sorted(out.items())]
