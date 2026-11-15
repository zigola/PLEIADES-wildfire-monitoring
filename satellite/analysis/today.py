"""Database layer of the A/B/C/D answer: reads stored NASA rows and import coverage, then calls ``estimate``.

Only real NASA rows are read (``scope='real'``, exact duplicates removed). Nothing is written here and no estimate
is ever stored as a ``Hotspot``. A day counts as *observed* only where a successful import covers it
(``FetchJob.covered_ranges``), so a missing observation is never turned into a zero.
"""

from __future__ import annotations

from datetime import timedelta

from django.db.models import Avg, Count, Max, Min, Sum

from satellite.analysis import coverage as cov, estimate as E
from satellite.analysis.params import SENSORS, hotspots
from satellite.models import Hotspot

MIN_FIRMS_DATE_YEAR = 2000  # MODIS Terra active-fire data start in November 2000


def _iso(v):
    return v.isoformat() if v else None


def _observed(qs, target):
    agg = qs.aggregate(latest=Max("acq_time"), first_import=Min("fetch__finished_at"), last_import=Max("fetch__finished_at"))
    products = sorted(p for p in qs.order_by().values_list("product", flat=True).distinct() if p)
    platforms = sorted({Hotspot.PLATFORMS.get((s or "").strip().lower(), s or "unknown")
                        for s in qs.order_by().values_list("satellite", flat=True).distinct()})
    latest = agg["latest"]
    return {"latest_acq_utc": f"{target.isoformat()}T{latest // 100:02d}:{latest % 100:02d}:00+00:00" if latest is not None else None,
            "first_imported_at": _iso(agg["first_import"]), "last_imported_at": _iso(agg["last_import"]),
            "products": products, "platforms": platforms}


def _history(bbox, sensor, spans, target, satellites, window):
    """({year: [daily counts]}, {year: count on the exact date}) for earlier, fully observed years."""
    daily, exact = {}, {}
    if not spans:
        return daily, exact
    for year in range(max(spans[0][0].year, MIN_FIRMS_DATE_YEAR), target.year):
        day = E.same_day_in_year(target, year)
        start, end = day - timedelta(days=window), day + timedelta(days=window)
        need = (end - start).days + 1
        observed = cov.covered_days(spans, start, end)
        if not observed:
            continue
        per_day = {r["acq_date"]: r["n"] for r in
                   hotspots(bbox=bbox, scope="real", start=start, end=end, sensors=[sensor], confidences=None,
                            satellites=satellites).order_by().values("acq_date").annotate(n=Count("id"))}
        if day in observed:
            exact[year] = per_day.get(day, 0)
        if len(observed) == need:  # a partly observed window would understate the counts: not used for the estimate
            daily[year] = [per_day.get(start + timedelta(days=i), 0) for i in range(need)]
    return daily, exact


def today_payload(bbox, target, today, sensors=SENSORS, satellites=None, window=E.WINDOW_HALF, min_years=E.MIN_YEARS):
    ranges = cov.coverage_ranges(bbox, "real")
    data = {}
    for sensor in sensors:
        spans = ranges.get(sensor, [])
        qs = hotspots(bbox=bbox, scope="real", start=target, end=target, sensors=[sensor], confidences=None,
                      satellites=satellites)
        n = qs.count()
        entry = {"detections": n, "covered": bool(cov.covered_days(spans, target, target)),
                 "observed": _observed(qs, target) if n else {}}
        entry["daily_by_year"], entry["exact_by_year"] = _history(bbox, sensor, spans, target, satellites, window)
        data[sensor] = entry
    payload = E.build_today_payload(target, today, data, min_years=min_years, window=window)
    payload["aoi"] = dict(zip(("south", "west", "north", "east"), bbox))
    payload["coverage"] = {s: [[a.isoformat(), b.isoformat()] for a, b in r] for s, r in ranges.items()}
    return payload


def same_date_table(f, target, window):
    """Per year, per sensor: detections and FRP statistics for the same calendar date (+/- window days)."""
    ranges = cov.coverage_ranges(f["bbox"], f["scope"])
    firsts = [r[0][0].year for r in ranges.values() if r]
    base = hotspots(**{**f, "start": None, "end": None})
    span = base.aggregate(a=Min("acq_date"))
    if span["a"]:
        firsts.append(span["a"].year)
    if not firsts:
        return []
    out = []
    for year in range(max(min(firsts), MIN_FIRMS_DATE_YEAR), target.year + 1):
        day = E.same_day_in_year(target, year)
        start, end = day - timedelta(days=window), day + timedelta(days=window)
        need = (end - start).days + 1
        rows = {r["sensor"]: r for r in hotspots(**{**f, "start": start, "end": end}).order_by().values("sensor").annotate(
            n=Count("id"), frp_n=Count("frp"), frp_sum=Sum("frp"), frp_avg=Avg("frp"), frp_max=Max("frp"))}
        entry = {"year": year, "date": day.isoformat(), "window_start": start.isoformat(), "window_end": end.isoformat(),
                 "this_year": year == target.year, "sensors": {}}
        for sensor in f["sensors"]:
            seen = len(cov.covered_days(ranges.get(sensor, []), start, end))
            r = rows.get(sensor)
            n = r["n"] if r else 0
            status = "detections" if n else ("zero" if seen == need else ("partial" if seen else "no_data"))
            frp = None
            if r and r["frp_n"]:
                frp = {"n": r["frp_n"], "sum_mw": round(r["frp_sum"], 1), "mean_mw": round(r["frp_avg"], 2), "max_mw": round(r["frp_max"], 1)}
            entry["sensors"][sensor] = {"status": status, "detections": n if (n or seen) else None, "frp": frp,
                                        "observed_days": seen, "window_days": need}
        out.append(entry)
    return out
