"""Scheduled NASA FIRMS synchronisation (the "every 10 minutes" worker).

Built on top of ``live.sync_recent`` (validation, duplicate detection, NRT/SP planning, harmonisation) and
``sync_policy`` (which satellites, how many days). This module adds what a scheduler needs:

* a ``SyncRun`` log row per check (status, window, requests, rows received/new, newest observation, errors);
* a global lock (a RUNNING row younger than ``LOCK_SECONDS``) so two overlapping runs never fetch at once;
* retries with exponential back-off, longer when NASA answers with its transaction limit;
* an incremental window (overlapping the previous one; duplicates are discarded by the importer);
* no estimate and no demo row is ever written here: only NASA rows reach the ``Hotspot`` table.

The per-area cooldown of ``live.sync_recent`` exists to protect the public "Refresh now" button; the scheduler
bypasses it with ``force=True`` because it already controls its own cadence.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone

from django.conf import settings
from django.utils import timezone as djtz

from satellite.analysis.params import hotspots
from satellite.models import SyncRun
from . import live, sync_policy
from .firms_client import FirmsError

log = logging.getLogger(__name__)
LOCK_SECONDS = 900
OK_STATUSES = (SyncRun.Status.OK, SyncRun.Status.PARTIAL)


def interval_seconds():
    return int(getattr(settings, "FIRMS_SYNC_INTERVAL_SECONDS", 600))


def last_ok():
    return SyncRun.objects.filter(status__in=OK_STATUSES, finished_at__isnull=False).order_by("-finished_at").first()


def is_locked(now=None):
    now = now or djtz.now()
    return SyncRun.objects.filter(status=SyncRun.Status.RUNNING, started_at__gte=now - timedelta(seconds=LOCK_SECONDS)).exists()


def newest_observation(bbox):
    """Newest stored real acquisition time (timezone-aware UTC) in the area, or None."""
    row = (hotspots(bbox=bbox, scope="real", confidences=None).order_by("-acq_date", "-acq_time")
           .values_list("acq_date", "acq_time").first())
    if not row:
        return None
    day, hhmm = row
    return datetime(day.year, day.month, day.day, hhmm // 100, hhmm % 100, tzinfo=timezone.utc)


def _status_of(result):
    """Map a ``live.sync_recent`` result to a SyncRun status."""
    st = result.get("status")
    if st in ("ok", "no_data_available"):
        return SyncRun.Status.OK
    if st == "partial":
        return SyncRun.Status.PARTIAL
    if st == "no_key":
        return SyncRun.Status.NO_KEY
    if st in ("running", "cooldown"):
        return SyncRun.Status.SKIPPED
    text = " ".join([result.get("message", "")] + [j.get("message", "") for j in result.get("jobs", [])]).lower()
    return SyncRun.Status.RATE_LIMITED if "transaction limit" in text else SyncRun.Status.FAILED


def run_once(client, bbox, days=None, families=live.LIVE_FAMILIES, trigger="scheduler", attempt=1, now=None, today=None):
    """One check of NASA FIRMS for ``bbox``. Always returns the (finished) ``SyncRun``; never raises FirmsError."""
    now = now or djtz.now()
    bbox = live.norm_bbox(bbox)
    if is_locked(now):
        return SyncRun.objects.create(status=SyncRun.Status.SKIPPED, trigger=trigger, attempt=attempt, bbox=list(bbox),
                                      finished_at=djtz.now(), message="Another synchronisation is still running; this check was skipped.")
    run = SyncRun.objects.create(trigger=trigger, attempt=attempt, bbox=list(bbox))
    today = today or live.utc_today()
    try:
        availability, note = {}, ""
        if client.configured:
            try:
                availability = client.availability("all")
            except FirmsError as exc:
                note = f"FIRMS data_availability unavailable ({exc}); all satellites requested."
        active, skipped = sync_policy.active_families(families, availability, today)
        previous = last_ok()
        if days is None:
            days = sync_policy.choose_days(previous.finished_at if previous else None, now)
        start, end = live.window(days, today)
        run.families, run.skipped_families, run.window_start, run.window_end = active, skipped, start, end
        run.availability = {k: [v[0].isoformat(), v[1].isoformat()] for k, v in availability.items()}
        run.save()
        if not active:
            result = {"status": "no_data_available", "message": "No satellite family is currently delivering data.",
                      "rows_received": 0, "rows_created": 0, "jobs": []}
        else:
            result = live.sync_recent(client, bbox, days, tuple(active), force=True, availability=availability or None, today=today)
        run.status = _status_of(result)
        run.requests_made = int(result.get("requests_made", 0))
        run.rows_received = int(result.get("rows_received", 0))
        run.rows_created = int(result.get("rows_created", 0))
        run.message = "\n".join(x for x in (note, result.get("message", "")) if x)[:2000]
        if skipped:
            run.message = (run.message + "\nSkipped: " + "; ".join(f"{k}: {v}" for k, v in skipped.items()))[:2000].strip()
    except Exception as exc:  # the log must record the failure, the scheduler must keep running
        log.exception("scheduled sync failed")
        run.status = SyncRun.Status.FAILED
        run.message = f"Unexpected error: {exc.__class__.__name__}"
    try:
        run.newest_observation = newest_observation(bbox)
    except Exception:
        log.exception("could not read newest observation")
    run.finished_at = djtz.now()
    run.save()
    return run


def run_with_retry(client, bbox, attempts=3, backoff=30, days=None, trigger="scheduler", sleep=time.sleep, today=None):
    """``run_once`` repeated with exponential back-off while the outcome is a failure or a rate limit.

    Returns the list of SyncRuns (one per attempt). A rate limit waits at least 60 s (the NASA budget is per 10 min).
    """
    runs = []
    for i in range(1, max(1, attempts) + 1):
        run = run_once(client, bbox, days=days, trigger=trigger, attempt=i, today=today)
        runs.append(run)
        if run.status not in (SyncRun.Status.FAILED, SyncRun.Status.RATE_LIMITED) or i >= attempts:
            break
        wait = backoff * (2 ** (i - 1))
        if run.status == SyncRun.Status.RATE_LIMITED:
            wait = max(wait, 60)
        log.warning("sync attempt %s ended %s; retrying in %s s", i, run.status, wait)
        sleep(wait)
    return runs


def status_payload(bbox=None):
    """JSON-safe synchronisation status for the interface (last check, last success, next check, newest data)."""
    now = djtz.now()
    last = SyncRun.objects.exclude(status=SyncRun.Status.SKIPPED).first()
    ok = last_ok()
    gained = SyncRun.objects.filter(status__in=OK_STATUSES, rows_created__gt=0).order_by("-finished_at").first()

    def iso(v):
        return v.isoformat() if v else None
    nxt = sync_policy.next_check(last.started_at, interval_seconds()) if last else None
    newest = newest_observation(bbox) if bbox else None
    if newest is None and ok:
        newest = ok.newest_observation
    return {
        "interval_seconds": interval_seconds(), "now_utc": now.isoformat(),
        "running": is_locked(now),
        "last_run": None if not last else {"id": last.id, "status": last.status, "started_at": iso(last.started_at),
                                           "finished_at": iso(last.finished_at), "rows_created": last.rows_created,
                                           "rows_received": last.rows_received, "trigger": last.trigger, "attempt": last.attempt,
                                           "message": last.message[:300]},
        "last_success_at": iso(ok.finished_at) if ok else None,
        "last_new_data_at": iso(gained.finished_at) if gained else None,
        "last_new_data_run": gained.id if gained else None,
        "next_check_utc": iso(nxt), "overdue": bool(nxt and nxt + timedelta(seconds=interval_seconds()) < now),
        "newest_observation_utc": iso(newest),
        "skipped_families": (ok.skipped_families if ok else {}) or {},
        "nasa_availability": (ok.availability if ok else {}) or {},
        "note": ("The check runs every few minutes, but NASA publishes detections only after each satellite overpass is "
                 "processed. The observation time is the satellite acquisition time; NASA does not provide a per-record "
                 "publication time, so the import time is the time this server stored the record."),
    }
