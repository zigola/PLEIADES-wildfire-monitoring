"""Import pipeline: FIRMS rows -> validated, de-duplicated Hotspot rows + FetchJob metadata."""

from __future__ import annotations

import csv
from collections import Counter
from datetime import date, timedelta

from django.db import transaction
from django.utils import timezone

from satellite.models import FetchJob, Hotspot
from . import firms_client as fc
from .parsing import RecordError, detect_sensor, normalize_row
from .products import PRODUCTS

CHUNK = 2000
KEY_FIELDS = ("latitude", "longitude", "acq_date", "acq_time", "satellite")


def _key(d):
    return (d["latitude"], d["longitude"], d["acq_date"], d["acq_time"], d["satellite"])


def store_rows(job, raw_rows, sensor, product):
    """Validate, de-duplicate and store rows. Updates the job counters. Returns rows created."""
    valid, invalid, in_batch_dups = {}, Counter(), 0
    received = 0
    for raw in raw_rows:
        received += 1
        try:
            rec = normalize_row(raw, sensor, product)
        except RecordError as exc:
            invalid[exc.code] += 1
            continue
        k = _key(rec)
        if k in valid:
            in_batch_dups += 1
            continue
        valid[k] = rec

    created = 0
    existing_dups = 0
    if valid:
        days = {k[2] for k in valid}
        with transaction.atomic():
            existing = set(Hotspot.objects.filter(product=product, is_demo=False, acq_date__in=days)
                           .values_list(*KEY_FIELDS))
            new = [Hotspot(fetch=job, source=f"NASA FIRMS {product}", **rec)
                   for k, rec in valid.items() if k not in existing]
            existing_dups = len(valid) - len(new)
            for i in range(0, len(new), CHUNK):
                Hotspot.objects.bulk_create(new[i:i + CHUNK], ignore_conflicts=True)
            created = len(new)

    job.rows_received += received
    job.rows_created += created
    job.rows_duplicate += existing_dups + in_batch_dups
    job.rows_invalid += sum(invalid.values())
    merged = Counter(job.invalid_reasons or {})
    merged.update(invalid)
    job.invalid_reasons = dict(merged)
    return created


def _finish(job, status, message=""):
    job.status = status
    job.finished_at = timezone.now()
    if message:
        job.message = (job.message + "\n" + message).strip()
    job.save()
    return job


def plan_ranges(family, start, end, availability):
    """Split [start, end] between the SP and NRT product of one family.

    family: 'MODIS', 'VIIRS_SNPP', 'VIIRS_NOAA20' or 'VIIRS_NOAA21'.
    SP is used where FIRMS says it is available, NRT after it. Dates outside both
    availability windows are dropped (and reported by the caller).
    Returns [(product_id, range_start, range_end)].
    """
    sp, nrt = f"{family}_SP", f"{family}_NRT"
    plan = []
    sp_rng, nrt_rng = availability.get(sp), availability.get(nrt)
    cursor = start
    if sp_rng:
        s, e = max(start, sp_rng[0]), min(end, sp_rng[1])
        if s <= e:
            plan.append((sp, s, e))
            cursor = e + timedelta(days=1)
    if nrt_rng and cursor <= end:
        s, e = max(cursor, nrt_rng[0]), min(end, nrt_rng[1])
        if s <= e:
            plan.append((nrt, s, e))
    return plan


def create_api_job(product, bbox, start, end, user=None, availability=None):
    meta = PRODUCTS[product]
    south, west, north, east = bbox
    return FetchJob.objects.create(
        kind=FetchJob.Kind.API, product=product, sensor=meta.sensor, south=south, west=west, north=north, east=east,
        start_date=start, end_date=end, source_label=f"FIRMS Area API / {product}",
        requested_by=user if getattr(user, "is_authenticated", False) else None,
        availability={k: [v[0].isoformat(), v[1].isoformat()] for k, v in (availability or {}).items() if k == product},
    )


def run_api_import(client, product, bbox, start, end, user=None, availability=None, job=None):
    """Import one FIRMS product for an AOI and period. Always returns the FetchJob."""
    meta = PRODUCTS[product]
    job = job or create_api_job(product, bbox, start, end, user, availability)
    if not client.configured:
        return _finish(job, FetchJob.Status.NO_KEY, "No FIRMS_MAP_KEY configured: nothing was requested.")
    covered, fatal = [], None
    try:
        for chunk_start, n, result in client.iter_days(product, bbox, start, end):
            chunk_end = chunk_start + timedelta(days=n - 1)
            if isinstance(result, Exception):
                job.days_failed += n
                job.message = (job.message + f"\n{chunk_start}..{chunk_end}: {result}").strip()
                continue
            store_rows(job, result, meta.sensor, product)
            job.days_ok += n
            if covered and date.fromisoformat(covered[-1][1]) + timedelta(days=1) == chunk_start:
                covered[-1][1] = chunk_end.isoformat()
            else:
                covered.append([chunk_start.isoformat(), chunk_end.isoformat()])
            job.covered_ranges = covered
            job.save(update_fields=["rows_received", "rows_created", "rows_duplicate", "rows_invalid",
                                    "invalid_reasons", "days_ok", "days_failed", "message", "covered_ranges"])
    except fc.FirmsError as exc:
        fatal = str(exc)
    job.requests_made = client.requests_made
    job.covered_ranges = covered
    if fatal:
        return _finish(job, FetchJob.Status.PARTIAL if job.days_ok else FetchJob.Status.FAILED, fatal)
    return _finish(job, FetchJob.Status.SUCCESS if not job.days_failed else FetchJob.Status.PARTIAL)


def import_csv(path, sensor=None, bbox=None, start=None, end=None, user=None, label=None):
    """Import a FIRMS CSV download. Coverage is declared (bbox/start/end) or inferred from the content."""
    with open(path, newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        sensor = (sensor or detect_sensor(reader.fieldnames))
        if not sensor:
            raise ValueError("Cannot tell if the file is MODIS or VIIRS; pass the sensor explicitly.")
        rows = list(reader)
    declared = bbox is not None and start is not None and end is not None
    product = "CSV_" + sensor  # product unknown for manual downloads; kept distinct from API products
    job = FetchJob.objects.create(
        kind=FetchJob.Kind.CSV, product=product[:24], sensor=sensor, source_label=(label or str(path).split("/")[-1])[:200],
        requested_by=user if getattr(user, "is_authenticated", False) else None, coverage_declared=declared)
    store_rows(job, rows, sensor, product)
    qs = Hotspot.objects.filter(fetch=job)
    if declared:
        job.south, job.west, job.north, job.east = bbox
        job.start_date, job.end_date = start, end
    elif job.rows_created:
        from django.db.models import Max, Min
        a = qs.aggregate(s=Min("latitude"), n=Max("latitude"), w=Min("longitude"), e=Max("longitude"),
                         d0=Min("acq_date"), d1=Max("acq_date"))
        job.south, job.north, job.west, job.east = a["s"], a["n"], a["w"], a["e"]
        job.start_date, job.end_date = a["d0"], a["d1"]
    if job.start_date:
        job.covered_ranges = [[job.start_date.isoformat(), job.end_date.isoformat()]]
    job.days_ok = ((job.end_date - job.start_date).days + 1) if job.start_date else 0
    return _finish(job, FetchJob.Status.SUCCESS if job.rows_created or not job.rows_invalid else FetchJob.Status.PARTIAL,
                   "" if declared else "Coverage inferred from file content (area = bounding box of detections).")
