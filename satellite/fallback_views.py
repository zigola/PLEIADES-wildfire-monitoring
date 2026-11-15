"""JSON endpoints of the fallback / synchronisation / historical-year features (GET /api/fire/...).

    /api/fire/today/            A/B/C/D answer for an area and a date (default: today, UTC)
    /api/fire/sync/status/      scheduled-synchronisation status (last check, next check, newest observation)
    /api/fire/history/years/    which years exist in the database for an area, per sensor and satellite
    /api/fire/same-date/        the same calendar date in every year: detections and FRP statistics

All of them are read-only. Estimates are computed on request from stored NASA rows; they are never stored.
"""

from __future__ import annotations

from datetime import date

from django.db.models import Count
from django.db.models.functions import ExtractYear
from django.http import JsonResponse

from .analysis import coverage as cov, today as T
from .analysis.params import (BadRequest, common_filters, hotspots, parse_bbox, parse_date, parse_int, parse_sensors,
                              require_single_category)
from .models import Hotspot
from .services import live, sync
from .views import api


def _satellites(get):
    return [s.strip() for s in get.get("satellite", "").split(",") if s.strip()] or None


def _target(get):
    today = live.utc_today()
    target = parse_date(get, "date", today)
    if target.year < T.MIN_FIRMS_DATE_YEAR:
        raise BadRequest("NASA FIRMS active-fire products start in November 2000; choose a later date.")
    return target, today


@api
def today_view(request):
    bbox = parse_bbox(request.GET)
    target, today = _target(request.GET)
    payload = T.today_payload(bbox, target, today, sensors=parse_sensors(request.GET), satellites=_satellites(request.GET),
                              window=parse_int(request.GET, "window", 7, 1, 15))
    payload["sync"] = sync.status_payload(bbox)
    return JsonResponse(payload, json_dumps_params={"default": str})


@api
def sync_status(request):
    bbox = parse_bbox(request.GET) if request.GET.get("south") else None
    return JsonResponse(sync.status_payload(bbox), json_dumps_params={"default": str})


@api
def history_years(request):
    f = common_filters(request.GET)
    f.update(start=None, end=None, confidences=None)
    rows = (hotspots(**f).order_by().annotate(y=ExtractYear("acq_date")).values("y", "sensor", "satellite")
            .annotate(n=Count("id")))
    years = {}
    for r in rows:
        e = years.setdefault(r["y"], {"year": r["y"], "total": 0, "MODIS": 0, "VIIRS": 0, "satellites": {}})
        e["total"] += r["n"]
        e[r["sensor"]] += r["n"]
        name = Hotspot.PLATFORMS.get((r["satellite"] or "").strip().lower(), r["satellite"] or "unknown")
        e["satellites"][name] = e["satellites"].get(name, 0) + r["n"]
    ranges = cov.coverage_ranges(f["bbox"], f["scope"]) if f["scope"] != "all" else {}
    return JsonResponse({"scope": f["scope"], "years": [years[y] for y in sorted(years)],
                         "coverage": {s: [[a.isoformat(), b.isoformat()] for a, b in r] for s, r in ranges.items()},
                         "note": "Counts are stored detections (exact duplicates removed, all confidence classes). "
                                 "Which years exist depends on what was imported and on each satellite's own start date."})


@api
def same_date(request):
    f = common_filters(request.GET)
    require_single_category(f)
    target, _today = _target(request.GET)
    window = parse_int(request.GET, "window", 0, 0, 15)
    return JsonResponse({"date": target.isoformat(), "window_days": window, "sensors": f["sensors"], "scope": f["scope"],
                         "years": T.same_date_table(f, target, window),
                         "note": "no_data = this date was never imported for the area (unknown, not zero); zero = imported and "
                                 "nothing detected; partial = only part of the window was imported. FRP statistics use only the "
                                 "records that provide FRP; MODIS and VIIRS are never added together."},
                        json_dumps_params={"default": str})
