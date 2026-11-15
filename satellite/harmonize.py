"""MODIS + VIIRS harmonization -> burning activity calendar.

Pure Python (no Django, no numpy) so it can be unit-tested on its own.

Why harmonize?
    MODIS (about 1 km pixels, 2000-present) and VIIRS (about 375 m pixels,
    2012-present) do not see fire the same way: VIIRS detects smaller and cooler
    fires and often reports several pixels where MODIS reports one. Raw hotspot
    counts from the two sensors therefore cannot be compared, and a naive merge
    shows a fake jump the day VIIRS starts.

Method
    1. Common grid: every detection is snapped to a 0.05 degree cell (about 5 km),
       coarser than both sensors' pixels.
    2. Common unit: the *active cell-day* (a cell with at least one detection on a
       given day). Counting cells, not pixels, removes the "many VIIRS pixels for
       one fire" effect and gives a burned-area-like activity measure.
    3. Cross-calibration: on the overlap period, scale = sum(MODIS cell-days) /
       sum(VIIRS cell-days). VIIRS cell-days times scale are "MODIS-equivalent".
    4. Harmonized daily index H(d): VIIRS x scale where VIIRS exists (better
       sensitivity), MODIS elsewhere. The series is continuous across 2012.
    5. Calendar statistics: 7-day bins by day-of-year, a leave-one-year-out
       climatology, z-scores for unusual periods and merged critical windows.
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import date, timedelta

GRID_DEG = 0.05
BIN_DAYS = 7
N_BINS = 52  # bins 0..51; the last bin absorbs day 365/366
MIN_OVERLAP_CELLDAYS = 50  # below this, fall back to a wider estimate
MIN_YEARS_FOR_BASELINE = 4
UNUSUAL_Z = 2.0
MIN_UNUSUAL_ACTIVITY = 5.0  # MODIS-equivalent cell-days per week

MODIS = "MODIS"
VIIRS = "VIIRS"


def cell_of(lat: float, lon: float) -> tuple[int, int]:
    return (math.floor(lat / GRID_DEG), math.floor(lon / GRID_DEG))


def cell_center(cell: tuple[int, int]) -> tuple[float, float]:
    return ((cell[0] + 0.5) * GRID_DEG, (cell[1] + 0.5) * GRID_DEG)


def week_bin(day: date) -> int:
    return min((day.timetuple().tm_yday - 1) // BIN_DAYS, N_BINS - 1)


def bin_start_doy(b: int) -> int:
    return b * BIN_DAYS + 1


def _bin_length(year: int, b: int) -> int:
    ndays = 366 if _is_leap(year) else 365
    start = bin_start_doy(b)
    end = ndays if b == N_BINS - 1 else start + BIN_DAYS - 1
    return end - start + 1


def _is_leap(y: int) -> bool:
    return y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)


def _date_from_doy(year: int, doy: int) -> date:
    return date(year, 1, 1) + timedelta(days=doy - 1)


def _daterange(a: date, b: date):
    d = a
    while d <= b:
        yield d
        d += timedelta(days=1)


def _mean(xs):
    return sum(xs) / len(xs) if xs else 0.0


def _std(xs):
    if len(xs) < 2:
        return 0.0
    m = _mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def pearson(xs, ys):
    n = len(xs)
    if n < 3:
        return None
    mx, my = _mean(xs), _mean(ys)
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    sy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if sx == 0 or sy == 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sx * sy)


# ---------------------------------------------------------------- aggregation

def cell_days(records):
    """records: iterable of (sensor, date, lat, lon, frp).

    Returns {sensor: {date: {cell: frp_sum}}}. Missing FRP counts as 0.
    """
    occ = {MODIS: defaultdict(dict), VIIRS: defaultdict(dict)}
    for sensor, day, lat, lon, frp in records:
        if sensor not in occ:
            continue
        cell = cell_of(lat, lon)
        per_day = occ[sensor][day]
        per_day[cell] = per_day.get(cell, 0.0) + (frp or 0.0)
    return occ


def daily_counts(occ_sensor):
    return {d: len(cells) for d, cells in occ_sensor.items()}


# --------------------------------------------------------------- calibration

def estimate_scale(modis_daily, viirs_daily, coverage, fallback=None):
    """MODIS-equivalent scale for one VIIRS cell-day.

    coverage = {"MODIS": (first, last) or None, "VIIRS": (first, last) or None}
    Returns dict(scale, method, overlap, overlap_days, r_weekly).
    """
    cm, cv = coverage.get(MODIS), coverage.get(VIIRS)
    out = {"scale": 1.0, "method": "none", "overlap": None,
           "overlap_days": 0, "r_weekly": None}
    if not cm or not cv:
        out["method"] = "single-sensor"
        return out
    start, end = max(cm[0], cv[0]), min(cm[1], cv[1])
    if start > end:
        out["method"] = "no-overlap"
        if fallback:
            out["scale"], out["method"] = fallback, "fallback"
        return out

    days = list(_daterange(start, end))
    m = [modis_daily.get(d, 0) for d in days]
    v = [viirs_daily.get(d, 0) for d in days]
    sm, sv = sum(m), sum(v)
    out["overlap"] = (start, end)
    out["overlap_days"] = len(days)

    weekly_m, weekly_v = defaultdict(float), defaultdict(float)
    for i, d in enumerate(days):
        key = (d - start).days // 7
        weekly_m[key] += m[i]
        weekly_v[key] += v[i]
    keys = sorted(weekly_m)
    out["r_weekly"] = pearson([weekly_m[k] for k in keys], [weekly_v[k] for k in keys])

    if sm >= MIN_OVERLAP_CELLDAYS and sv >= MIN_OVERLAP_CELLDAYS:
        out["scale"], out["method"] = sm / sv, "area-overlap"
    elif fallback:
        out["scale"], out["method"] = fallback, "fallback"
    else:
        out["method"] = "insufficient-overlap"
    return out


# ----------------------------------------------------------------- harmonize

def harmonize(modis_daily, viirs_daily, coverage, scale):
    """Return {date: (H, M, V)} over the union of sensor coverage."""
    spans = [c for c in coverage.values() if c]
    if not spans:
        return {}
    first = min(s[0] for s in spans)
    last = max(s[1] for s in spans)
    cv = coverage.get(VIIRS)
    cm = coverage.get(MODIS)
    out = {}
    for d in _daterange(first, last):
        m = modis_daily.get(d, 0)
        v = viirs_daily.get(d, 0)
        if cv and cv[0] <= d <= cv[1]:
            h = v * scale
        elif cm and cm[0] <= d <= cm[1]:
            h = float(m)
        else:
            continue
        out[d] = (h, m, v)
    return out


# ------------------------------------------------------------- calendar stats

def weekly_matrix(series):
    """{year: {bin: weekly activity}} using only fully covered bins.

    Weekly activity = mean daily H x 7 so the 8/9-day last bin is comparable.
    """
    sums = defaultdict(lambda: defaultdict(float))
    counts = defaultdict(lambda: defaultdict(int))
    for d, (h, _m, _v) in series.items():
        b = week_bin(d)
        sums[d.year][b] += h
        counts[d.year][b] += 1
    out = {}
    for y in sums:
        row = {}
        for b, total in sums[y].items():
            if counts[y][b] == _bin_length(y, b):
                row[b] = total / counts[y][b] * BIN_DAYS
        out[y] = row
    return out


def climatology(matrix):
    """Per-bin mean/std/n across years (all years)."""
    out = []
    for b in range(N_BINS):
        vals = [matrix[y][b] for y in matrix if b in matrix[y]]
        out.append({"bin": b, "start_doy": bin_start_doy(b),
                    "mean": _mean(vals), "std": _std(vals), "n": len(vals)})
    return out


def anomalies(matrix, min_activity=MIN_UNUSUAL_ACTIVITY, z_threshold=UNUSUAL_Z):
    """Leave-one-year-out z-scores. A year never contributes to its own baseline."""
    found = []
    for y in sorted(matrix):
        for b, x in matrix[y].items():
            others = [matrix[o][b] for o in matrix if o != y and b in matrix[o]]
            if len(others) < MIN_YEARS_FOR_BASELINE:
                continue
            mu, sd = _mean(others), _std(others)
            sd = max(sd, math.sqrt(max(mu, 1.0)))  # Poisson-like floor: no huge z on tiny counts
            z = (x - mu) / sd
            if z >= z_threshold and x >= min_activity:
                start = _date_from_doy(y, bin_start_doy(b))
                end = start + timedelta(days=_bin_length(y, b) - 1)
                found.append({"year": y, "bin": b, "start": start.isoformat(),
                              "end": end.isoformat(), "value": round(x, 1),
                              "baseline": round(mu, 1), "z": round(z, 2),
                              "level": "extreme" if z >= 3 else "unusual"})
    found.sort(key=lambda a: -a["z"])
    return found


def critical_periods(clim, fraction=0.5):
    """Merge adjacent bins whose typical activity is >= fraction of the peak."""
    usable = [c for c in clim if c["n"] >= 2]
    if not usable:
        return []
    peak = max(c["mean"] for c in usable)
    if peak < 1.0:
        return []
    thr = peak * fraction
    hot = sorted(c["bin"] for c in usable if c["mean"] >= thr)
    groups, cur = [], []
    for b in hot:
        if cur and b - cur[-1] > 1:
            groups.append(cur)
            cur = []
        cur.append(b)
    if cur:
        groups.append(cur)

    by_bin = {c["bin"]: c for c in clim}
    ref = 2001  # non-leap reference year, only to print month-day
    out = []
    for g in groups:
        start = _date_from_doy(ref, bin_start_doy(g[0]))
        last_bin = g[-1]
        end_doy = 365 if last_bin == N_BINS - 1 else bin_start_doy(last_bin) + BIN_DAYS - 1
        end = _date_from_doy(ref, end_doy)
        peak_bin = max(g, key=lambda x: by_bin[x]["mean"])
        out.append({"start_md": start.strftime("%m-%d"), "end_md": end.strftime("%m-%d"),
                    "weeks": len(g), "typical_weekly": round(_mean([by_bin[x]["mean"] for x in g]), 1),
                    "peak_start_md": _date_from_doy(ref, bin_start_doy(peak_bin)).strftime("%m-%d"),
                    "peak_weekly": round(by_bin[peak_bin]["mean"], 1)})
    out.sort(key=lambda p: -p["typical_weekly"])
    return out


# ------------------------------------------------------------------- summary

def monthly_tables(series):
    """{year: {"modis": [12], "viirs": [12], "harmonized": [12]}} (raw = cell-days)."""
    tab = defaultdict(lambda: {"modis": [0] * 12, "viirs": [0] * 12, "harmonized": [0.0] * 12})
    for d, (h, m, v) in series.items():
        row = tab[d.year]
        row["modis"][d.month - 1] += m
        row["viirs"][d.month - 1] += v
        row["harmonized"][d.month - 1] += h
    for row in tab.values():
        row["harmonized"] = [round(x, 1) for x in row["harmonized"]]
    return dict(tab)


def build_summary(records, coverage, fallback_scale=None):
    """records: iterable of (sensor, date, lat, lon, frp). coverage: sensor -> (first,last)."""
    occ = cell_days(records)
    md, vd = daily_counts(occ[MODIS]), daily_counts(occ[VIIRS])
    cal = estimate_scale(md, vd, coverage, fallback=fallback_scale)
    series = harmonize(md, vd, coverage, cal["scale"])
    matrix = weekly_matrix(series)
    clim = climatology(matrix)

    daily = {}
    for d, (h, m, v) in series.items():
        if h or m or v:
            daily.setdefault(str(d.year), []).append(
                [d.timetuple().tm_yday, round(h, 2), m, v])

    month_mean = [0.0] * 12
    full_years = [y for y in matrix if len(matrix[y]) >= N_BINS - 1]
    monthly = monthly_tables(series)
    for m in range(12):
        vals = [monthly[y]["harmonized"][m] for y in full_years if y in monthly]
        month_mean[m] = round(_mean(vals), 1)

    return {
        "years": sorted({d.year for d in series}),
        "harmonization": {
            "scale": round(cal["scale"], 4), "method": cal["method"],
            "overlap_days": cal["overlap_days"],
            "overlap": [x.isoformat() for x in cal["overlap"]] if cal["overlap"] else None,
            "r_weekly": None if cal["r_weekly"] is None else round(cal["r_weekly"], 3),
            "grid_deg": GRID_DEG,
        },
        "coverage": {k: [x.isoformat() for x in v] for k, v in coverage.items() if v},
        "daily": daily,
        "monthly": {str(y): v for y, v in sorted(monthly.items())},
        "climatology": {
            "bins": [{**c, "mean": round(c["mean"], 2), "std": round(c["std"], 2)} for c in clim],
            "monthly_mean": month_mean,
            "baseline_years": len(full_years),
        },
        "critical_periods": critical_periods(clim),
        "anomalies": anomalies(matrix)[:40],
        "totals": {
            "records_cells": sum(len(c) for s in occ.values() for c in s.values()),
            "active_days": sum(1 for v in series.values() if v[0] > 0),
        },
    }
