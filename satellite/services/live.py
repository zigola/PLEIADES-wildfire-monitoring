"""On-demand "live" synchronisation with NASA FIRMS (today + the last few days).

What it does
  * pulls the most recent days (default: today and the 2 days before, UTC) for an area,
    for MODIS and the VIIRS satellites, using the NRT product where FIRMS says NRT is the
    only one available (SP is used automatically for older days);
  * is idempotent: the importer already de-duplicates, so pulling "today" ten times only
    adds the detections that arrived in between;
  * is rate-limited per area (cooldown) so a public button cannot burn the FIRMS budget
    (5000 transactions / 10 min per MAP_KEY);
  * never touches IoT data and never writes synthetic rows.

Historical imports keep using ``tasks.import_family`` / ``import_firms``; this module only
adds the "give me the latest data now" path.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from django.conf import settings
from django.utils import timezone as djtz

from satellite.models import FetchJob
from . import importer
from .firms_client import FirmsError

log = logging.getLogger(__name__)

LIVE_LABEL = "LIVE sync"
LIVE_FAMILIES = ("MODIS", "VIIRS_SNPP", "VIIRS_NOAA20", "VIIRS_NOAA21")
MIN_DAYS, MAX_DAYS, DEFAULT_DAYS = 1, 10, 3
STALE_RUNNING_SECONDS = 300  # a RUNNING job older than this is considered dead
OK = (FetchJob.Status.SUCCESS, FetchJob.Status.PARTIAL)


def cooldown_seconds():
    return int(getattr(settings, "FIRMS_LIVE_COOLDOWN_SECONDS", 600))


def utc_today():
    """FIRMS dates are UTC days; never use the server's local date."""
    return datetime.now(timezone.utc).date()


def window(days=DEFAULT_DAYS, today=None):
    days = max(MIN_DAYS, min(MAX_DAYS, int(days)))
    today = today or utc_today()
    return today - timedelta(days=days - 1), today


def norm_bbox(bbox):
    """Stable 4-decimal bbox so the same area always matches itself in the cooldown lookup."""
    return tuple(round(float(v), 4) for v in bbox)


def _live_jobs(bbox):
    s, w, n, e = norm_bbox(bbox)
    return FetchJob.objects.filter(kind=FetchJob.Kind.API, source_label__startswith=LIVE_LABEL,
                                   south=s, west=w, north=n, east=e)


def last_sync(bbox):
    """Finished time of the latest successful live sync of this exact area, or None."""
    job = (_live_jobs(bbox).filter(status__in=OK, finished_at__isnull=False, days_ok__gt=0)
           .order_by("-finished_at").first())  # days_ok > 0: a sync where every request failed does not start a cooldown
    return job.finished_at if job else None


def is_running(bbox, now=None):
    now = now or djtz.now()
    return _live_jobs(bbox).filter(status=FetchJob.Status.RUNNING,
                                   started_at__gte=now - timedelta(seconds=STALE_RUNNING_SECONDS)).exists()


def retry_after(bbox, now=None):
    """Seconds until a new live sync of this area is allowed (0 = allowed now)."""
    now = now or djtz.now()
    last = last_sync(bbox)
    if not last:
        return 0
    return max(0, int(cooldown_seconds() - (now - last).total_seconds()))


def sync_recent(client, bbox, days=DEFAULT_DAYS, families=LIVE_FAMILIES, user=None, force=False,
                availability=None, harmonize=True, today=None):
    """Fetch the latest days for ``bbox``. Returns a JSON-safe dict (never raises FirmsError).

    status: 'ok' | 'partial' | 'failed' | 'cooldown' | 'running' | 'no_key' | 'no_data_available'
    """
    bbox = norm_bbox(bbox)
    start, end = window(days, today)
    base = {"bbox": list(bbox), "start": start.isoformat(), "end": end.isoformat(), "jobs": [], "rows_created": 0,
            "rows_received": 0}
    if not client.configured:
        return {**base, "status": "no_key", "message": "No FIRMS_MAP_KEY is configured on the server."}
    if is_running(bbox):
        return {**base, "status": "running", "message": "A live sync of this area is already running."}
    wait = 0 if force else retry_after(bbox)
    if wait:
        return {**base, "status": "cooldown", "retry_after": wait,
                "message": f"This area was synchronised recently; next sync allowed in {wait} s."}

    if availability is None:
        try:
            availability = client.availability("all")
        except FirmsError as exc:
            availability = {}
            log.warning("data_availability unavailable: %s", exc)
    jobs = []
    for family in families:
        if availability:
            plan = importer.plan_ranges(family, start, end, availability)
        else:  # availability table unreachable: the newest days only exist as NRT anyway
            plan = [(f"{family}_NRT", start, end)]
        for product, s, e in plan:
            job = importer.create_api_job(product, bbox, s, e, user, availability)
            job.source_label = f"{LIVE_LABEL} / {product}"
            job.save(update_fields=["source_label"])
            jobs.append(job)
    if not jobs:
        return {**base, "status": "no_data_available",
                "message": "FIRMS reports no data for these satellites in the requested days."}

    before = client.requests_made
    for job in jobs:
        importer.run_api_import(client, job.product, bbox, job.start_date, job.end_date, user, availability, job)
        job.refresh_from_db()
    base["requests_made"] = client.requests_made - before
    base["jobs"] = [{"id": j.id, "product": j.product, "status": j.status, "start": j.start_date.isoformat(),
                     "end": j.end_date.isoformat(), "received": j.rows_received, "created": j.rows_created,
                     "duplicates": j.rows_duplicate, "invalid": j.rows_invalid, "message": j.message[:300]}
                    for j in jobs]
    base["rows_created"] = sum(j.rows_created for j in jobs)
    base["rows_received"] = sum(j.rows_received for j in jobs)
    if all(j.status == FetchJob.Status.SUCCESS for j in jobs):
        base["status"] = "ok"
    elif any(j.days_ok > 0 for j in jobs):
        base["status"] = "partial"
    else:
        base["status"] = "failed"
        base["message"] = next((j.message for j in jobs if j.message), "NASA FIRMS did not answer.")[:300]

    if harmonize and base["rows_created"]:
        try:  # flag NRT/SP duplicates and MODIS/VIIRS overlaps for the refreshed window only
            from satellite.harmonization import pipeline
            pipeline.run(start - timedelta(days=1), end, "real")
        except Exception:  # the data is stored; harmonization can be re-run from the data page
            log.exception("post-sync harmonization failed")
            base["harmonization"] = "failed"
    return base
