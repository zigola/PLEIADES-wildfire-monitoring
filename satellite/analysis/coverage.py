"""Which days were actually observed (queried) for an AOI, per sensor.

FIRMS has no 'observation log'. POLARIS therefore records, for every import,
which area and dates were successfully requested (FetchJob.covered_ranges).
A day is 'observed' for a sensor when a successful import whose area contains
the AOI covers that day. A day with zero detections that is observed is a real
zero; a day that was never queried is *missing data*, not zero fire.
"""

from __future__ import annotations

from datetime import date, timedelta

from satellite.models import FetchJob

OK_STATUSES = (FetchJob.Status.SUCCESS, FetchJob.Status.PARTIAL)


def _contains(job, aoi):
    s, w, n, e = aoi
    return job.has_bbox and job.south <= s and job.west <= w and job.north >= n and job.east >= e


def coverage_ranges(aoi, scope):
    """{sensor: [(start, end), ...]} merged, for the jobs matching the data scope."""
    kinds = {"real": [FetchJob.Kind.API, FetchJob.Kind.CSV], "demo": [FetchJob.Kind.DEMO],
             "all": [FetchJob.Kind.API, FetchJob.Kind.CSV, FetchJob.Kind.DEMO]}.get(scope, [])
    raw = {"MODIS": [], "VIIRS": []}
    for job in FetchJob.objects.filter(status__in=OK_STATUSES, kind__in=kinds):
        if not job.sensor or job.sensor not in raw or not _contains(job, aoi):
            continue
        for a, b in job.covered_ranges or []:
            try:
                raw[job.sensor].append((date.fromisoformat(a), date.fromisoformat(b)))
            except ValueError:
                continue
    out = {}
    for sensor, spans in raw.items():
        spans.sort()
        merged = []
        for a, b in spans:
            if merged and a <= merged[-1][1] + timedelta(days=1):
                merged[-1] = (merged[-1][0], max(merged[-1][1], b))
            else:
                merged.append((a, b))
        out[sensor] = merged
    return out


def covered_days(ranges, start, end):
    """Set of dates in [start, end] covered by merged ranges."""
    days = set()
    for a, b in ranges:
        d, stop = max(a, start), min(b, end)
        while d <= stop:
            days.add(d)
            d += timedelta(days=1)
    return days


def coverage_bounds(ranges):
    """{sensor: (first, last)} for sensors that have any coverage (input of harmonize.estimate_scale)."""
    return {s: (r[0][0], r[-1][1]) for s, r in ranges.items() if r}
