"""Pure decisions of the scheduled NASA FIRMS synchronisation (no Django, no network).

* ``active_families``: a satellite family whose newest published day in the FIRMS ``data_availability`` table is older
  than ``STALE_DAYS`` is skipped and reported. This is how Suomi-NPP is handled after NASA stopped delivering it
  (announced for 2 November 2026): its history stays in the database, but it is no longer requested every cycle and a
  quiet family is not reported as an outage.
* ``choose_days``: incremental window. FIRMS NRT rows can arrive late, so the window always overlaps the previous one;
  the importer's duplicate detection keeps repeated rows out. After a long pause the window grows (max 10 days).
"""

from __future__ import annotations

from datetime import datetime, timedelta

STALE_DAYS = 5
MIN_DAYS, MAX_DAYS = 1, 10
REGULAR_DAYS = 2      # normal cycle: today + yesterday (UTC)
CATCH_UP_AFTER_HOURS = 12


def active_families(families, availability, today, stale_days=STALE_DAYS):
    """Return (active, skipped) where skipped maps family -> reason.

    ``availability`` is {product_id: (min_date, max_date)}; an empty table (FIRMS unreachable) skips nothing.
    """
    if not availability:
        return list(families), {}
    active, skipped = [], {}
    for family in families:
        spans = [availability.get(f"{family}_NRT"), availability.get(f"{family}_SP")]
        spans = [s for s in spans if s]
        if not spans:
            skipped[family] = "not listed in the FIRMS data availability table"
            continue
        newest = max(s[1] for s in spans)
        if newest < today - timedelta(days=stale_days):
            skipped[family] = (f"no new data published since {newest.isoformat()}; "
                               f"NASA appears to have stopped delivering this satellite (history is kept)")
            continue
        active.append(family)
    return active, skipped


def choose_days(last_ok_finished, now, regular=REGULAR_DAYS):
    """Number of UTC days to request, counted back from today.

    No previous success: a 3-day first window. Recent success: ``regular`` days of overlap. Long gap: cover the gap
    plus one day of overlap, capped at 10 (older gaps need ``import_history``).
    """
    if last_ok_finished is None:
        return 3
    gap = now - last_ok_finished
    if gap <= timedelta(hours=CATCH_UP_AFTER_HOURS):
        return max(MIN_DAYS, min(MAX_DAYS, regular))
    days = gap.days + 2
    return max(MIN_DAYS, min(MAX_DAYS, max(days, regular)))


def next_check(last_started, interval_seconds):
    """When the next scheduled check is due (None if there was never one)."""
    if not isinstance(last_started, datetime):
        return None
    return last_started + timedelta(seconds=interval_seconds)
