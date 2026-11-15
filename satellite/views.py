"""Public read-only JSON API of the satellite module (GET /api/fire/...).

Operator-only actions (import, harmonization) live in satellite/pages.py.
All endpoints share the filters of ``analysis.params.common_filters`` and never mix
synthetic with NASA rows unless ``scope=all`` is requested explicitly.
"""

from __future__ import annotations

import csv
from datetime import date, timedelta

from django.core.paginator import Paginator
from django.db.models import Max, Min
from django.http import JsonResponse, StreamingHttpResponse
from django.views.decorators.http import require_GET

from . import harmonize as H
from .analysis import quality as dq
from .analysis import anomalies as an, calendar as cal, coverage as cov, overview as ov, spatial, temporal
from .analysis.params import (DEFAULT_BBOX, BadRequest, common_filters, data_mode, hotspots, parse_bbox, parse_conf, require_single_category,
                              parse_date, parse_float_range, parse_int)
from .models import FetchJob, Hotspot

MAX_CELLS = 4000
MAX_GEOJSON = 5000
EXPORT_LIMIT = 500_000


def api(view):
    """Turn BadRequest into HTTP 400 JSON and enforce GET."""
    @require_GET
    def wrapper(request, *a, **kw):
        try:
            return view(request, *a, **kw)
        except BadRequest as exc:
            return JsonResponse({"error": str(exc)}, status=400)
        except ValueError as exc:
            return JsonResponse({"error": str(exc)}, status=400)
    wrapper.__name__ = view.__name__
    wrapper.__doc__ = view.__doc__
    return wrapper


def _range(f, default_days=None):
    """Concrete [start, end] for a request: explicit, else the data span of the selection."""
    start, end = f["start"], f["end"]
    if start is None or end is None:
        span = hotspots(**{**f, "start": None, "end": None}).aggregate(a=Min("acq_date"), b=Max("acq_date"))
        if span["a"] is None:
            raise BadRequest("No data for this selection; import data first.")
        end = end or span["b"]
        start = start or (max(span["a"], end - timedelta(days=default_days - 1)) if default_days else span["a"])
    if start > end:
        raise BadRequest("'start' must not be after 'end'.")
    return start, end


def _error(message):
    return JsonResponse({"error": message}, status=400)


# ------------------------------------------------------------------ calendar summary (existing)

def _coverage_fallback(f):
    ranges = cov.coverage_ranges(f["bbox"], f["scope"])
    bounds = cov.coverage_bounds(ranges)
    if bounds:
        return bounds
    out = {}  # legacy rows imported before FetchJob existed: use the data span
    for row in hotspots(bbox=None, scope=f["scope"]).values("sensor").annotate(first=Min("acq_date"), last=Max("acq_date")):
        out[row["sensor"]] = (row["first"], row["last"])
    return out


def _records(qs):
    return qs.order_by().values_list("sensor", "acq_date", "latitude", "longitude", "frp").iterator(chunk_size=5000)


@api
def fire_summary(request):
    f = common_filters(request.GET)
    require_single_category(f)
    f["start"] = f["end"] = None
    mode = data_mode()
    aoi = dict(zip(("south", "west", "north", "east"), f["bbox"]))
    if f["scope"] == "empty":
        return JsonResponse({"has_data": False, "data_mode": mode, "scope": "empty", "aoi": aoi})
    coverage = _coverage_fallback(f)
    qs = hotspots(**f)
    summary = H.build_summary(list(_records(qs)), coverage)
    if summary["harmonization"]["method"] == "insufficient-overlap":
        region = hotspots(**{**f, "bbox": DEFAULT_BBOX})
        occ = H.cell_days(_records(region))
        calib = H.estimate_scale(H.daily_counts(occ[H.MODIS]), H.daily_counts(occ[H.VIIRS]), coverage)
        if calib["method"] == "area-overlap":
            summary = H.build_summary(list(_records(qs)), coverage, calib["scale"])
    summary.update({"has_data": True, "data_mode": mode, "scope": f["scope"], "aoi": aoi,
                    "min_conf": request.GET.get("min_conf", "n")})
    return JsonResponse(summary)


@api
def fire_cells(request):
    f = common_filters(request.GET)
    start = f["start"] or date(1900, 1, 1)
    end = f["end"] or date(2999, 12, 31)
    qs = hotspots(**{**f, "start": start, "end": end})
    cells = {}
    for sensor, day, lat, lon, frp in _records(qs):
        e = cells.setdefault(H.cell_of(lat, lon), {"days": set(), "frp": 0.0, "sensors": set()})
        e["days"].add(day)
        e["frp"] += frp or 0.0
        e["sensors"].add(sensor[0])
    rows = sorted(cells.items(), key=lambda kv: -len(kv[1]["days"]))
    out = []
    for cell, e in rows[:MAX_CELLS]:
        lat, lon = H.cell_center(cell)
        out.append({"lat": round(lat, 4), "lon": round(lon, 4), "days": len(e["days"]),
                    "frp": round(e["frp"], 1), "sensors": "".join(sorted(e["sensors"]))})
    return JsonResponse({"cells": out, "truncated": len(rows) > MAX_CELLS, "total_cells": len(rows),
                         "grid_deg": H.GRID_DEG})


# ------------------------------------------------------------------------------ overview etc.

def _detection(h):
    d = {"id": h.id, "lat": h.latitude, "lon": h.longitude, "date": h.acq_date.isoformat(), "time_utc": h.acq_hhmm,
         "sensor": h.sensor, "satellite": h.platform, "satellite_code": h.satellite,
         "instrument": h.instrument or h.sensor, "daynight": h.daynight or None, "category": h.category,
         "product": h.product or None, "source": h.source or None}
    # only fields actually present
    for key, val in (("confidence", h.confidence_raw or h.confidence or None), ("confidence_class", h.confidence or None),
                     ("frp_mw", h.frp), ("brightness_k", h.brightness), ("brightness_2_k", h.brightness_2),
                     ("scan_km", h.scan), ("track_km", h.track), ("version", h.version or None),
                     ("overlap_group", h.overlap_group), ("import_id", h.fetch_id),
                     ("imported_at", h.fetch.finished_at.isoformat() if h.fetch_id and h.fetch and h.fetch.finished_at else None)):
        if val is not None:
            d[key] = val
    return d


@api
def overview(request):
    f = common_filters(request.GET)
    return JsonResponse(ov.overview(f), json_dumps_params={"default": str})


@api
def detections(request):
    """Paginated list, or GeoJSON for the map (format=geojson, capped at 5000 points)."""
    f = common_filters(request.GET)
    qs = hotspots(**f).order_by("-acq_date", "-acq_time", "id").select_related("fetch")
    if request.GET.get("format") == "geojson":
        total = qs.count()
        feats = [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [h.longitude, h.latitude]},
                  "properties": _detection(h)} for h in qs[:MAX_GEOJSON]]
        return JsonResponse({"type": "FeatureCollection", "features": feats, "total": total,
                             "truncated": total > MAX_GEOJSON, "limit": MAX_GEOJSON})
    size = parse_int(request.GET, "page_size", 50, 1, 500)
    page = Paginator(qs, size).get_page(request.GET.get("page", 1))
    return JsonResponse({"results": [_detection(h) for h in page], "page": page.number,
                         "pages": page.paginator.num_pages, "total": page.paginator.count})


@api
def day_detail(request):
    f = common_filters(request.GET)
    day = parse_date(request.GET, "date")
    if not day:
        raise BadRequest("'date' is required (YYYY-MM-DD).")
    f.update(start=day, end=day)
    qs = hotspots(**f).order_by("acq_time", "sensor", "id").select_related("fetch")
    size = parse_int(request.GET, "page_size", 100, 1, 500)
    page = Paginator(qs, size).get_page(request.GET.get("page", 1))
    c = cal.build_calendar(f, day, day)["days"][0]
    return JsonResponse({"date": day.isoformat(), "status": c["status"], "modis": c["modis"], "viirs": c["viirs"],
                         "covered": c["covered"], "results": [_detection(h) for h in page], "page": page.number,
                         "pages": page.paginator.num_pages, "total": page.paginator.count})


@api
def calendar_view(request):
    f = common_filters(request.GET)
    require_single_category(f)
    start, end = _range(f, default_days=366)
    if (end - start).days > 3660:
        raise BadRequest("Calendar range is limited to 10 years per request.")
    return JsonResponse(cal.build_calendar(f, start, end))


@api
def compare_view(request):
    f = common_filters(request.GET)
    require_single_category(f)
    a0, a1 = parse_date(request.GET, "start"), parse_date(request.GET, "end")
    b0, b1 = parse_date(request.GET, "compare_start"), parse_date(request.GET, "compare_end")
    if not all((a0, a1, b0, b1)):
        raise BadRequest("start, end, compare_start and compare_end are required.")
    if a0 > a1 or b0 > b1:
        raise BadRequest("A period start must not be after its end.")
    if max((a1 - a0).days, (b1 - b0).days) > 731:
        raise BadRequest("Each period is limited to 2 years.")
    return JsonResponse(cal.compare_periods(f, a0, a1, b0, b1))


@api
def harmonization(request):
    f = common_filters(request.GET)
    require_single_category(f)
    params = {"max_minutes": parse_int(request.GET, "max_minutes", 180, 10, 720),
              "max_radius_km": parse_float_range(request.GET, "max_radius_km", 6.0, 0.5, 10.0)}
    return JsonResponse(ov.harmonization_stats(f, params), json_dumps_params={"default": str})


@api
def clusters(request):
    f = common_filters(request.GET)
    require_single_category(f)
    eps_km = parse_float_range(request.GET, "eps_km", 2.0, 0.3, 10.0)
    eps_h = parse_float_range(request.GET, "eps_hours", 24.0, 1.0, 168.0)
    min_pts = parse_int(request.GET, "min_pts", 3, 2, 50)
    return JsonResponse(spatial.clusters(hotspots(**f), eps_km, eps_h, min_pts))


@api
def spatial_view(request):
    f = common_filters(request.GET)
    require_single_category(f)
    dens = spatial.density(hotspots(**f))
    return JsonResponse({**dens, "repeated": spatial.repeated_activity(dens, parse_int(request.GET, "min_days", 5, 2, 365)),
                         "sensor_comparison": spatial.sensor_cell_comparison(dens)})


@api
def temporal_view(request):
    f = common_filters(request.GET)
    qs = hotspots(**f)
    period = request.GET.get("period", "month")
    if period not in ("day", "week", "month"):
        raise BadRequest("'period' must be day, week or month.")
    return JsonResponse({"series": temporal.aggregate(qs, period), "period": period, "seasonal": temporal.seasonal(qs),
                         "yearly": temporal.yearly(qs), "acquisition": temporal.hour_histogram(qs),
                         "note": "Periods without detections are absent from the series; they may be zero OR "
                                 "unobserved. Use /api/fire/calendar/ to tell them apart."})


@api
def anomalies_view(request):
    f = common_filters(request.GET)
    require_single_category(f)
    start, end = _range(f, default_days=366)
    return JsonResponse({"daily": an.daily_rolling(f, start, end), "weekly": an.historical_weekly(f),
                         "limitations": an.LIMITATIONS})


@api
def quality_view(request):
    return JsonResponse(dq.quality_report(), json_dumps_params={"default": str})


@api
def imports_view(request):
    rows = []
    for j in FetchJob.objects.all()[:100]:
        rows.append({"id": j.id, "kind": j.kind, "kind_label": j.get_kind_display(), "product": j.product, "sensor": j.sensor,
                     "status": j.status, "status_label": j.get_status_display(),
                     "bbox": [j.south, j.west, j.north, j.east] if j.has_bbox else None,
                     "start": j.start_date, "end": j.end_date, "started_at": j.started_at, "finished_at": j.finished_at,
                     "received": j.rows_received, "created": j.rows_created, "duplicates": j.rows_duplicate,
                     "invalid": j.rows_invalid, "invalid_reasons": j.invalid_reasons, "days_ok": j.days_ok,
                     "days_failed": j.days_failed, "message": j.message, "source": j.source_label,
                     "coverage_declared": j.coverage_declared,
                     "days_total": ((j.end_date - j.start_date).days + 1) if j.start_date and j.end_date else None,
                     "progress_pct": (min(100, round(100 * (j.days_ok + j.days_failed) / ((j.end_date - j.start_date).days + 1)))
                                      if j.start_date and j.end_date else None)})
    return JsonResponse({"imports": rows}, json_dumps_params={"default": str})


class _Echo:
    def write(self, value):
        return value


EXPORT_FIELDS = ["id", "sensor", "satellite", "satellite_code", "instrument", "latitude", "longitude", "acq_date",
                 "acq_time_utc", "frp_mw", "brightness_k", "brightness_2_k", "scan_km", "track_km", "confidence_raw",
                 "confidence_class", "daynight", "product", "version", "data_category", "overlap_group", "import_id"]


@api
def export_csv(request):
    f = common_filters(request.GET)
    qs = hotspots(**f).order_by("acq_date", "acq_time", "id")
    if qs.count() > EXPORT_LIMIT:
        raise BadRequest(f"Export limited to {EXPORT_LIMIT} rows; narrow the area or period.")
    writer = csv.writer(_Echo())

    def rows():
        yield writer.writerow(EXPORT_FIELDS)
        for h in qs.iterator(chunk_size=5000):
            yield writer.writerow([h.id, h.sensor, h.platform, h.satellite, h.instrument or h.sensor, h.latitude, h.longitude,
                                   h.acq_date.isoformat(), h.acq_hhmm, h.frp, h.brightness, h.brightness_2, h.scan, h.track,
                                   h.confidence_raw, h.confidence, h.daynight, h.product, h.version, h.category,
                                   h.overlap_group, h.fetch_id])
    resp = StreamingHttpResponse(rows(), content_type="text/csv; charset=utf-8")
    resp["Content-Disposition"] = 'attachment; filename="polaris_detections.csv"'
    return resp
