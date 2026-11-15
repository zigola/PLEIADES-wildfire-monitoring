"""Burning activity calendar: per-day status, counts, anomalies and summaries.

Day status (per AOI, sensors and data scope):
    no_data    no successful import covers this day for any selected sensor  -> unknown, NOT zero
    zero       at least one selected sensor was queried for this day and found nothing
    detections at least one active detection (exact duplicates removed)
Each day also carries ``covered`` (sensors with coverage) so a partly observed day is visible,
and ``high`` when it is a statistical outlier against the trailing 28 days.
Every number is a count of stored rows (traceable through /api/fire/day/ and /api/fire/detections/).
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import date, timedelta

from satellite import harmonize as H
from satellite.analysis import coverage as cov
from satellite.analysis.params import hotspots

ROLLING_WINDOW = 28
MIN_ROLLING_DAYS = 14
ROLLING_Z = 3.0
MIN_HIGH_DETECTIONS = 5


def daily_table(qs):
    """{date: {"MODIS": {"n", "frp", "cells"}, "VIIRS": {...}}} in one streaming pass."""
    table = defaultdict(lambda: {"MODIS": {"n": 0, "frp": 0.0, "cells": set()},
                                 "VIIRS": {"n": 0, "frp": 0.0, "cells": set()}})
    for sensor, day, lat, lon, frp in qs.order_by().values_list(
            "sensor", "acq_date", "latitude", "longitude", "frp").iterator(chunk_size=5000):
        e = table[day][sensor]
        e["n"] += 1
        e["frp"] += frp or 0.0
        e["cells"].add(H.cell_of(lat, lon))
    return table


def _rolling_flags(days, values, covered):
    """z-score of each day against the previous 28 observed days (Poisson-like floor on the deviation)."""
    flags = {}
    for i, d in enumerate(days):
        if d not in covered:
            continue
        prev = [values[days[j]] for j in range(max(0, i - ROLLING_WINDOW), i) if days[j] in covered]
        if len(prev) < MIN_ROLLING_DAYS:
            continue
        mu = sum(prev) / len(prev)
        sd = math.sqrt(sum((x - mu) ** 2 for x in prev) / (len(prev) - 1)) if len(prev) > 1 else 0.0
        sd = max(sd, math.sqrt(max(mu, 1.0)))
        z = (values[d] - mu) / sd
        flags[d] = (round(z, 2), round(mu, 1), round(sd, 2))
    return flags


def build_calendar(filters, start, end):
    """Calendar payload for [start, end] using the shared filters (see params.common_filters)."""
    sensors = filters["sensors"]
    aoi, scope = filters["bbox"], filters["scope"]
    qs = hotspots(**{**filters, "start": start, "end": end})
    table = daily_table(qs)
    ranges = cov.coverage_ranges(aoi, scope)
    covered_by = {s: cov.covered_days(ranges.get(s, []), start, end) for s in sensors}
    all_days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    any_covered = set().union(*covered_by.values()) if covered_by else set()
    # a day with detections is observed by definition, even if the coverage record is incomplete
    observed = any_covered | set(table)
    totals = {d: sum(table[d][s]["n"] for s in sensors) if d in table else 0 for d in all_days}
    flags = _rolling_flags(all_days, totals, observed)

    days, months = [], defaultdict(lambda: {"modis": 0, "viirs": 0, "active_days": 0, "zero_days": 0,
                                           "no_data_days": 0, "high_days": 0, "days": 0})
    for d in all_days:
        row = table.get(d)
        m = row["MODIS"]["n"] if row and "MODIS" in sensors else 0
        v = row["VIIRS"]["n"] if row and "VIIRS" in sensors else 0
        total = m + v
        sensors_ok = [s for s in sensors if d in covered_by[s]]
        status = "detections" if total else ("zero" if d in any_covered else "no_data")
        z = flags.get(d)
        high = bool(z and z[0] >= ROLLING_Z and total >= MIN_HIGH_DETECTIONS)
        mo = months[d.strftime("%Y-%m")]
        mo["days"] += 1
        mo["modis"] += m
        mo["viirs"] += v
        mo[{"detections": "active_days", "zero": "zero_days", "no_data": "no_data_days"}[status]] += 1
        mo["high_days"] += high
        days.append({"date": d.isoformat(), "status": status, "modis": m, "viirs": v, "total": total,
                     "cells_modis": len(row["MODIS"]["cells"]) if row else 0,
                     "cells_viirs": len(row["VIIRS"]["cells"]) if row else 0,
                     "frp_modis": round(row["MODIS"]["frp"], 1) if row else 0.0,
                     "frp_viirs": round(row["VIIRS"]["frp"], 1) if row else 0.0,
                     "covered": sensors_ok, "high": high,
                     "z": z[0] if z else None, "baseline": z[1] if z else None})
    return {
        "start": start.isoformat(), "end": end.isoformat(), "sensors": sensors, "scope": scope,
        "days": days, "months": dict(sorted(months.items())),
        "summary": {"days": len(days), "active_days": sum(d["status"] == "detections" for d in days),
                    "zero_days": sum(d["status"] == "zero" for d in days),
                    "no_data_days": sum(d["status"] == "no_data" for d in days),
                    "high_days": sum(d["high"] for d in days),
                    "modis": sum(d["modis"] for d in days), "viirs": sum(d["viirs"] for d in days)},
        "coverage": {s: [[a.isoformat(), b.isoformat()] for a, b in r] for s, r in ranges.items()},
        "method": {"status": "no_data = never queried for this area/date; zero = queried, nothing found",
                   "high_day": f"z >= {ROLLING_Z} vs the previous {ROLLING_WINDOW} observed days "
                               f"(needs >= {MIN_ROLLING_DAYS}); deviation floored at sqrt(max(mean,1)); "
                               f"and >= {MIN_HIGH_DETECTIONS} detections",
                   "counts": "stored detections after removing exact duplicates; MODIS and VIIRS are not added into one unit"},
    }


def compare_periods(filters, a_start, a_end, b_start, b_end):
    """Totals and day-aligned series for two periods (e.g. the same months of two years)."""
    out = {}
    for key, (s, e) in {"a": (a_start, a_end), "b": (b_start, b_end)}.items():
        cal = build_calendar(filters, s, e)
        out[key] = {"start": cal["start"], "end": cal["end"], "summary": cal["summary"],
                    "series": [{"day": i + 1, "date": d["date"], "modis": d["modis"], "viirs": d["viirs"],
                                "status": d["status"]} for i, d in enumerate(cal["days"])]}
    for key in ("modis", "viirs"):
        a, b = out["a"]["summary"][key], out["b"]["summary"][key]
        out.setdefault("change", {})[key] = None if not b else round((a - b) / b * 100, 1)
    out["note"] = ("Percentage change is only meaningful when both periods were observed for the same number of "
                   "days; see no_data_days in each summary.")
    return out
