"""Historical-based statistical estimates and the A/B/C/D availability classification.

This module is deliberately free of Django imports so it can be unit-tested anywhere.

Categories (never mixed, never displayed as one another)
    A  VERIFIED NASA OBSERVATIONS      stored FIRMS rows for the requested date and area, or a day that was
                                       successfully queried and returned nothing (a verified zero).
    B  HISTORICAL OBSERVATIONS         stored FIRMS rows for the same calendar date in PREVIOUS years.
    C  HISTORICAL-BASED STATISTICAL ESTIMATE
                                       an expectation computed from category B. It is NOT a satellite detection,
                                       has no coordinates and does not say that a fire exists.
    D  DATA CURRENTLY UNAVAILABLE      nothing observed and not enough history for a reliable estimate.

What the estimate is: for the area and the calendar day, the typical number of satellite thermal-anomaly
detections per day in a +/- ``window`` day window around that day, over earlier years in which the area was
actually observed. It is not a forecast of a fire and says nothing about where or when one will start.
"""

from __future__ import annotations

import math
from datetime import date

LABELS = {
    "A": "VERIFIED NASA OBSERVATIONS",
    "B": "HISTORICAL OBSERVATIONS",
    "C": "HISTORICAL-BASED STATISTICAL ESTIMATE (not a satellite detection)",
    "D": "DATA CURRENTLY UNAVAILABLE",
}

MIN_YEARS = 5          # minimum number of fully observed earlier years
WINDOW_HALF = 7        # +/- days around the calendar day
Z95 = 1.959964


def percentile(values, q):
    """Linear-interpolation percentile (q in 0..100) of a non-empty list."""
    if not values:
        raise ValueError("percentile of an empty list")
    xs = sorted(values)
    if len(xs) == 1:
        return float(xs[0])
    pos = (len(xs) - 1) * q / 100.0
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(xs) - 1)
    return float(xs[lo] + (xs[hi] - xs[lo]) * (pos - lo))


def wilson(successes, n, z=Z95):
    """Wilson score interval for a proportion; (None, None) when n == 0."""
    if n <= 0:
        return None, None
    p = successes / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, centre - half), min(1.0, centre + half)


def same_day_in_year(target, year):
    """The same month/day in ``year`` (29 February falls back to 28 February)."""
    try:
        return date(year, target.month, target.day)
    except ValueError:
        return date(year, 2, 28)


def estimate(daily_by_year, min_years=MIN_YEARS, window=WINDOW_HALF):
    """Estimate from ``{year: [detections per day across the window]}``.

    Only years in which every day of the window was observed may be passed in (the caller checks coverage), so
    a missing observation is never counted as zero. Returns a dict with ``status`` "estimate" or "insufficient".
    """
    years = sorted(y for y, counts in daily_by_year.items() if counts)
    n_years = len(years)
    n_detections = int(sum(sum(daily_by_year[y]) for y in years))
    base = {"n_years": n_years, "years": years, "n_detections": n_detections, "window_days": 2 * window + 1,
            "min_years_required": min_years,
            "reference_period": [years[0], years[-1]] if years else None}
    if n_years < min_years:
        return {**base, "status": "insufficient",
                "reason": (f"Only {n_years} earlier year(s) with complete observation of this area around this date; "
                           f"at least {min_years} are required, so a reliable estimate cannot be generated.")}

    per_year_mean = [sum(daily_by_year[y]) / len(daily_by_year[y]) for y in years]
    mean = sum(per_year_mean) / n_years
    sd = math.sqrt(sum((x - mean) ** 2 for x in per_year_mean) / (n_years - 1)) if n_years > 1 else 0.0
    p10, p50, p90 = (percentile(per_year_mean, q) for q in (10, 50, 90))
    days = [c for y in years for c in daily_by_year[y]]
    days_with = sum(1 for c in days if c > 0)
    years_with = sum(1 for x in per_year_mean if x > 0)
    lo, hi = wilson(years_with, n_years)
    cv = (sd / mean) if mean > 0 else None
    return {
        **base, "status": "estimate",
        "indicators": {
            "mean_detections_per_day": round(mean, 3), "median_detections_per_day": round(p50, 3),
            "std_between_years": round(sd, 3), "coefficient_of_variation": None if cv is None else round(cv, 3),
            "p10_detections_per_day": round(p10, 3), "p90_detections_per_day": round(p90, 3),
            "min_year_mean": round(min(per_year_mean), 3), "max_year_mean": round(max(per_year_mean), 3),
            "share_of_days_with_detection": round(days_with / len(days), 4),
            "years_with_any_detection": years_with,
            "share_of_years_with_detection": round(years_with / n_years, 4),
            "share_of_years_with_detection_95ci": [None if lo is None else round(lo, 4), None if hi is None else round(hi, 4)],
        },
        "per_year_mean_detections_per_day": {str(y): round(m, 3) for y, m in zip(years, per_year_mean)},
        "methodology": (f"Mean number of detections per day in a +/-{window} day window around the calendar date, "
                        f"computed for each earlier year in which the whole window was observed, then summarised across "
                        f"years (mean, median, 10th-90th percentile range of the yearly means, share of years with at "
                        f"least one detection with a 95% Wilson interval). MODIS and VIIRS are never added together."),
        "uncertainty": (f"The 10th-90th percentile range of the yearly means is "
                        f"{round(p10, 3)}-{round(p90, 3)} detections per day (between-year standard deviation "
                        f"{round(sd, 3)}). With {n_years} years the percentiles are coarse."),
        "limitations": [
            "This is an expectation derived from past years, not an observation and not a forecast of a specific fire.",
            "It has no location: no coordinate is inferred and nothing here indicates that a fire exists today.",
            "Weather, drought, fuel, land use and sensor changes between years are not modelled.",
            "Days inside one year are correlated, so the day-level share is descriptive and has no confidence interval.",
            "Detection counts depend on overpass times, cloud cover and the sensor; counts of different sensors are not comparable.",
        ],
    }


def build_today_payload(target, today, sensors, min_years=MIN_YEARS, window=WINDOW_HALF):
    """Assemble the A/B/C/D answer.

    ``sensors`` maps a sensor name to a dict with
        detections   int, stored detections for ``target`` in the area (confidence not filtered)
        covered      bool, ``target`` was successfully queried for this sensor and area
        observed     optional extra facts about the stored detections (latest time, products, import times)
        daily_by_year  {year: [counts]} for earlier years with complete coverage of the window
        exact_by_year  {year: count} for the exact calendar date (earlier years with coverage)
    """
    result = {"date": target.isoformat(), "today_utc": today.isoformat(), "labels": LABELS, "sensors": []}
    future = target > today
    result["is_future"] = future
    result["is_today"] = target == today
    primary_rank = {"A": 0, "C": 1, "D": 2}
    best = "D"
    for name in sorted(sensors):
        s = sensors[name]
        entry = {"sensor": name, "date": target.isoformat(), "historical": [
            {"year": y, "detections": n, "date": same_day_in_year(target, y).isoformat()}
            for y, n in sorted((s.get("exact_by_year") or {}).items())]}
        if future:
            entry.update(category="D", label=LABELS["D"], reason="The requested date is in the future; no observation can exist and "
                                                                "a statistical estimate for a future date is not offered.")
        elif s.get("detections", 0) > 0:
            entry.update(category="A", label=LABELS["A"], detections=int(s["detections"]), observed=s.get("observed") or {},
                         note="Satellite thermal anomalies published by NASA FIRMS for this date and area; "
                              "not a confirmed wildfire." + (" Today's day is still being published." if target == today else ""))
        elif s.get("covered"):
            entry.update(category="A", label=LABELS["A"], detections=0, verified_zero=True,
                         note="NASA FIRMS was queried for this date and area and returned no detection"
                              + (" so far (today is not complete)." if target == today else ".")
                              + " Clouds and overpass times can hide fires.")
        else:
            est = estimate(s.get("daily_by_year") or {}, min_years=min_years, window=window)
            if est["status"] == "estimate":
                entry.update(category="C", label=LABELS["C"], estimate=est,
                             note="NASA FIRMS has no observation stored for this date and area yet. This is a statistical "
                                  "estimate from earlier years and is replaced automatically by verified observations once "
                                  "they are imported.")
            else:
                entry.update(category="D", label=LABELS["D"], reason=est["reason"], estimate=None)
        if primary_rank[entry["category"]] < primary_rank[best]:
            best = entry["category"]
        result["sensors"].append(entry)
    if not sensors:
        best = "D"
        result["reason"] = "No sensor was evaluated."
    result["primary_category"] = best
    result["has_historical"] = any(e["historical"] for e in result["sensors"])
    result["disclaimer"] = ("A hotspot is a satellite thermal anomaly, not a confirmed wildfire. Historical observations belong "
                            "to earlier dates and are not current. A statistical estimate is not an observation.")
    return result
