"""MODIS + VIIRS harmonization pipeline (record level).

Stages for a date range and a data scope (real or demo, never mixed):

  1. LOAD       rows are already validated and normalized at import (services/parsing.py):
                one schema, UTC timestamps, sensor and original satellite preserved.
  2. EXACT DUP  rows describing the same observation (same sensor, platform, latitude,
                longitude, UTC date and time) but delivered by different products, e.g.
                the NRT copy of a detection later reprocessed as SP, are linked:
                the preferred copy (SP > manual CSV > NRT, then lowest id) stays active,
                the others get ``exact_dup_of``. Nothing is deleted.
  3. OVERLAP    active MODIS and VIIRS detections that plausibly describe the same heat
                source receive a shared ``overlap_group``:
                    great-circle distance <= (pixel_modis + pixel_viirs) / 2   (footprint overlap)
                    |time difference|     <= max_minutes (default 180)
                where pixel = max(scan, track) in km from the record (fallback 1.0 km MODIS,
                0.375 km VIIRS). Groups are connected components of matching pairs.
                Overlap is a *flag*: neither record is removed and neither FRP nor brightness
                is merged or averaged (they are different instruments and channels).
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import date, timedelta

from django.db import transaction
from django.utils import timezone

from satellite.models import HarmonizationRun, Hotspot

DEFAULT_PARAMS = {"max_minutes": 180, "coincident_minutes": 30, "default_pixel_km": {"MODIS": 1.0, "VIIRS": 0.375},
                  "max_radius_km": 6.0, "chunk_days": 31, "version": "1"}
PREFERENCE = {"SP": 0, "CSV": 1, "NRT": 2}
EARTH_KM = 6371.0088


def haversine_km(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_KM * math.asin(min(1.0, math.sqrt(a)))


def obs_minutes(day, hhmm):
    """Minutes since 1970-01-01 00:00 UTC of an observation."""
    return (day - date(1970, 1, 1)).days * 1440 + (hhmm // 100) * 60 + hhmm % 100


def _pref(product):
    for tag in ("SP", "NRT"):
        if product.endswith("_" + tag):
            return PREFERENCE[tag]
    return PREFERENCE["CSV"]


def find_exact_duplicates(rows):
    """rows: (id, sensor, satellite, lat, lon, date, time, product). Returns {dup_id: kept_id}."""
    groups = defaultdict(list)
    for rid, sensor, sat, lat, lon, day, hhmm, product in rows:
        platform = Hotspot.PLATFORMS.get((sat or "").strip().lower(), sat)
        groups[(sensor, platform, lat, lon, day, hhmm)].append((_pref(product or ""), rid))
    out = {}
    for members in groups.values():
        if len(members) < 2:
            continue
        members.sort()
        keep = members[0][1]
        for _p, rid in members[1:]:
            out[rid] = keep
    return out


def find_overlaps(rows, params=None):
    """rows: (id, sensor, lat, lon, minutes, scan, track). Returns ({row_id: group_id}, pairs_matched).

    Only MODIS-VIIRS pairs create links. Group id = smallest member id (deterministic).
    """
    p = {**DEFAULT_PARAMS, **(params or {})}
    cell = 0.05
    grid = defaultdict(list)
    modis, viirs = [], []
    for r in rows:
        (modis if r[1] == "MODIS" else viirs).append(r)
    for r in viirs:
        grid[(math.floor(r[2] / cell), math.floor(r[3] / cell))].append(r)

    def pixel(sensor, scan, track):
        vals = [v for v in (scan, track) if v]
        return max(vals) if vals else p["default_pixel_km"][sensor]

    parent = {}

    def find(x):
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    pairs = 0
    for m in modis:
        pm = pixel("MODIS", m[5], m[6])
        reach = min(p["max_radius_km"], (pm + 0.9) / 2 + 0.01)  # 0.9 km: widest VIIRS pixel
        span = int(reach / (111.0 * cell * max(0.2, math.cos(math.radians(m[2]))))) + 1
        ci, cj = math.floor(m[2] / cell), math.floor(m[3] / cell)
        for i in range(ci - span, ci + span + 1):
            for j in range(cj - span, cj + span + 1):
                for v in grid.get((i, j), ()):
                    if abs(v[4] - m[4]) > p["max_minutes"]:
                        continue
                    radius = min(p["max_radius_km"], (pm + pixel("VIIRS", v[5], v[6])) / 2)
                    if haversine_km(m[2], m[3], v[2], v[3]) <= radius:
                        ra, rb = find(m[0]), find(v[0])
                        if ra != rb:
                            parent[max(ra, rb)] = min(ra, rb)
                        pairs += 1
    return {rid: find(rid) for rid in list(parent)}, pairs


def run(start, end, scope="real", params=None, progress=None):
    """Annotate Hotspot rows in [start, end]. Idempotent. Returns the HarmonizationRun."""
    if scope not in ("real", "demo"):
        raise ValueError("scope must be 'real' or 'demo'; the two are never harmonized together")
    p = {**DEFAULT_PARAMS, **(params or {})}
    run_obj = HarmonizationRun.objects.create(start_date=start, end_date=end, scope=scope, parameters=p)
    stats = {"rows": 0, "exact_duplicates": 0, "overlap_groups": 0, "overlapping_rows": 0, "pairs_matched": 0}
    try:
        base = Hotspot.objects.filter(is_demo=(scope == "demo"))
        d = start
        while d <= end:
            d1 = min(end, d + timedelta(days=p["chunk_days"] - 1))
            with transaction.atomic():
                chunk = base.filter(acq_date__gte=d, acq_date__lte=d1)
                chunk.update(exact_dup_of=None, overlap_group=None)
                rows = list(chunk.values_list("id", "sensor", "satellite", "latitude", "longitude",
                                              "acq_date", "acq_time", "product"))
                stats["rows"] += len(rows)
                dups = find_exact_duplicates(rows)
                for kept in set(dups.values()):
                    Hotspot.objects.filter(id__in=[k for k, v in dups.items() if v == kept]).update(exact_dup_of_id=kept)
                stats["exact_duplicates"] += len(dups)

                margin = base.filter(acq_date__gte=d - timedelta(days=1), acq_date__lte=d1 + timedelta(days=1),
                                     exact_dup_of__isnull=True)
                raw = margin.values_list("id", "sensor", "latitude", "longitude", "acq_date", "acq_time", "scan", "track")
                active = [(i, s, la, lo, obs_minutes(day, t), sc, tr) for i, s, la, lo, day, t, sc, tr in raw
                          if i not in dups]
                groups, pairs = find_overlaps(active, p)
                in_chunk = {r[0] for r in rows}
                members = [(rid, g) for rid, g in groups.items() if rid in in_chunk]
                by_group = defaultdict(list)
                for rid, g in members:
                    by_group[g].append(rid)
                for g, ids in by_group.items():
                    Hotspot.objects.filter(id__in=ids).update(overlap_group=g)
                stats["overlap_groups"] += len(by_group)
                stats["overlapping_rows"] += len(members)
                stats["pairs_matched"] += pairs
            if progress:
                progress(d1)
            d = d1 + timedelta(days=1)
        run_obj.status = "success"
    except Exception as exc:  # recorded, then re-raised
        run_obj.status, run_obj.message = "failed", f"{exc.__class__.__name__}: {exc}"
        raise
    finally:
        run_obj.stats = stats
        run_obj.finished_at = timezone.now()
        run_obj.save()
    return run_obj
