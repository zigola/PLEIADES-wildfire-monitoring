"""Transparent anomaly reporting. Each item states period, baseline, metric, threshold, sources,
why it was flagged and its limitations. An anomaly is a statistical outlier, never an emergency."""

from __future__ import annotations

from datetime import date, timedelta

from satellite import harmonize as H
from satellite.analysis import coverage as cov
from satellite.analysis.calendar import ROLLING_WINDOW, ROLLING_Z, build_calendar
from satellite.analysis.params import hotspots

LIMITATIONS = ("A statistical outlier is not an emergency: it can come from more overpasses or fewer clouds, "
               "agricultural burning campaigns, a data gap in the baseline, or a long-burning single event. "
               "It does not predict fire spread.")


def historical_weekly(filters, limit=40):
    """Weekly leave-one-year-out anomalies on the harmonized cell-day index (needs >= 4 other years)."""
    qs = hotspots(**{**filters, "start": None, "end": None})
    records = list(qs.order_by().values_list("sensor", "acq_date", "latitude", "longitude", "frp").iterator(chunk_size=5000))
    ranges = cov.coverage_ranges(filters["bbox"], filters["scope"])
    bounds = cov.coverage_bounds(ranges)
    if not bounds and records:  # legacy rows without import metadata: fall back to data span
        for s in ("MODIS", "VIIRS"):
            ds = [r[1] for r in records if r[0] == s]
            if ds:
                bounds[s] = (min(ds), max(ds))
    summary = H.build_summary(records, bounds) if records else None
    items = []
    if summary:
        for a in summary["anomalies"][:limit]:
            items.append({
                "method": "historical-leave-one-year-out", "period": [a["start"], a["end"]],
                "metric": "harmonized active 5 km cell-days per week (MODIS-equivalent)",
                "value": a["value"], "baseline": a["baseline"],
                "baseline_description": f"mean of the same week in all other years ({summary['climatology']['baseline_years']} complete years)",
                "threshold": f"z >= {H.UNUSUAL_Z} and >= {H.MIN_UNUSUAL_ACTIVITY} cell-days", "z": a["z"], "level": a["level"],
                "sources": "MODIS and VIIRS combined through the calibration factor "
                           f"x{summary['harmonization']['scale']} ({summary['harmonization']['method']})",
                "why": f"{a['value']} active cell-days vs {a['baseline']} typical for this week (z = {a['z']}).",
                "limitations": LIMITATIONS})
    return items


def daily_rolling(filters, start, end, limit=40):
    cal = build_calendar(filters, start, end)
    items = []
    for d in cal["days"]:
        if d["high"]:
            items.append({
                "method": "rolling-28-day", "period": [d["date"], d["date"]],
                "metric": "daily detections (MODIS + VIIRS counted separately, then summed for the threshold)",
                "value": d["total"], "baseline": d["baseline"],
                "baseline_description": f"mean of the previous {ROLLING_WINDOW} observed days",
                "threshold": f"z >= {ROLLING_Z} and >= 5 detections", "z": d["z"],
                "level": "extreme" if d["z"] >= 5 else "unusual",
                "sources": {"MODIS": d["modis"], "VIIRS": d["viirs"]},
                "why": f"{d['total']} detections (MODIS {d['modis']}, VIIRS {d['viirs']}) vs about {d['baseline']} "
                       f"on a typical recent day (z = {d['z']}).",
                "limitations": LIMITATIONS + " Raw counts mix sensors with different pixel sizes; check each separately."})
    items.sort(key=lambda a: -a["z"])
    return items[:limit]
