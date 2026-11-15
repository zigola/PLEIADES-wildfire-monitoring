"""Data Quality and Validation report. Every number is computed from the database at request time.

Each metric is {"value": number | None, "available": bool, "note": str}. ``None`` / available=False means
"cannot be determined" (for example, no NASA rows exist yet); it is never rendered as zero.
"""

from __future__ import annotations

from django.db.models import Count, Max, Min, Q, Sum

from satellite.models import FetchJob, Hotspot


def _m(value, note="", available=True):
    return {"value": value if available else None, "available": available, "note": note}


def _section(qs, label):
    n = qs.count()
    if not n:
        na = "No rows in this category."
        return {k: _m(None, na, False) for k in ("total", "modis", "viirs", "missing_confidence", "missing_frp", "missing_brightness",
                                                  "low_confidence", "out_of_range_coordinates", "date_first", "date_last", "days_with_data")}
    a = qs.aggregate(
        modis=Count("id", filter=Q(sensor="MODIS")), viirs=Count("id", filter=Q(sensor="VIIRS")),
        no_conf=Count("id", filter=Q(confidence="")), no_frp=Count("id", filter=Q(frp__isnull=True)),
        no_bt=Count("id", filter=Q(brightness__isnull=True)), low=Count("id", filter=Q(confidence="l")),
        bad=Count("id", filter=Q(latitude__lt=-90) | Q(latitude__gt=90) | Q(longitude__lt=-180) | Q(longitude__gt=180)),
        first=Min("acq_date"), last=Max("acq_date"))
    days = qs.values("acq_date").distinct().count()
    return {"total": _m(n), "modis": _m(a["modis"]), "viirs": _m(a["viirs"]),
            "missing_confidence": _m(a["no_conf"], "confidence not provided by the source product"),
            "missing_frp": _m(a["no_frp"]), "missing_brightness": _m(a["no_bt"]),
            "low_confidence": _m(a["low"], "low class of each sensor's own scale"),
            "out_of_range_coordinates": _m(a["bad"], "stored rows outside valid latitude/longitude; rejected rows are counted under imports"),
            "date_first": _m(a["first"].isoformat()), "date_last": _m(a["last"].isoformat()),
            "days_with_data": _m(days, "distinct acquisition dates that contain at least one detection")}


def quality_report():
    all_qs = Hotspot.objects.all()
    real = all_qs.filter(is_demo=False, exact_dup_of__isnull=True)
    demo = all_qs.filter(is_demo=True, exact_dup_of__isnull=True)
    total = all_qs.count()
    flagged = all_qs.filter(exact_dup_of__isnull=False).count()
    jobs = FetchJob.objects.exclude(kind=FetchJob.Kind.DEMO)
    n_jobs = jobs.count()
    ok_jobs = jobs.filter(status__in=(FetchJob.Status.SUCCESS, FetchJob.Status.PARTIAL)).order_by("-finished_at")
    last_ok = ok_jobs.filter(kind=FetchJob.Kind.API).first()
    last_any = ok_jobs.first()
    agg = jobs.aggregate(rec=Sum("rows_received"), cre=Sum("rows_created"), dup=Sum("rows_duplicate"), inv=Sum("rows_invalid"),
                         failed=Count("id", filter=Q(status=FetchJob.Status.FAILED)), partial=Count("id", filter=Q(status=FetchJob.Status.PARTIAL)),
                         nokey=Count("id", filter=Q(status=FetchJob.Status.NO_KEY)), dfail=Sum("days_failed"))
    reasons = {}
    for r in jobs.exclude(invalid_reasons={}).values_list("invalid_reasons", flat=True):
        for k, v in (r or {}).items():
            reasons[k] = reasons.get(k, 0) + v
    errors = [{"id": j.id, "when": j.started_at, "product": j.product, "status": j.status, "message": (j.message or "")[:300]}
              for j in jobs.filter(Q(status__in=(FetchJob.Status.FAILED, FetchJob.Status.PARTIAL, FetchJob.Status.NO_KEY))).order_by("-started_at")[:10]]
    has_jobs = n_jobs > 0
    return {
        "database": {
            "total_rows": _m(total, "all stored rows, including flagged duplicate copies"),
            "real_nasa": _m(real.count(), "NASA FIRMS rows (API or CSV), unique", True),
            "synthetic_demo": _m(demo.count(), "generated demonstration rows, NOT NASA observations"),
            "modis": _m(real.filter(sensor="MODIS").count(), "real NASA only", real.exists()),
            "viirs": _m(real.filter(sensor="VIIRS").count(), "real NASA only", real.exists()),
            "modis_synthetic": _m(demo.filter(sensor="MODIS").count(), "synthetic", demo.exists()),
            "viirs_synthetic": _m(demo.filter(sensor="VIIRS").count(), "synthetic", demo.exists()),
            "duplicates_flagged": _m(flagged, "exact duplicates across products: kept in the database, excluded from analysis"),
        },
        "real": _section(real, "real"),
        "synthetic": _section(demo, "synthetic"),
        "imports": {
            "count": _m(n_jobs),
            "last_successful_nasa_import": _m(last_ok.finished_at.isoformat() if last_ok and last_ok.finished_at else None,
                                              f"{last_ok.product} {last_ok.start_date} to {last_ok.end_date}" if last_ok else "no successful NASA API import yet",
                                              bool(last_ok and last_ok.finished_at)),
            "last_successful_any_real_import": _m(last_any.finished_at.isoformat() if last_any and last_any.finished_at else None,
                                                  "API or CSV", bool(last_any and last_any.finished_at)),
            "rows_received": _m(agg["rec"], "", has_jobs), "rows_created": _m(agg["cre"], "", has_jobs),
            "rows_duplicate_skipped": _m(agg["dup"], "already stored: not imported twice", has_jobs),
            "rows_rejected": _m(agg["inv"], "failed validation (invalid coordinates, dates, times, FRP, numbers)", has_jobs),
            "rejected_by_reason": reasons if has_jobs else None,
            "failed_imports": _m(agg["failed"], "", has_jobs), "partial_imports": _m(agg["partial"], "some date chunks failed", has_jobs),
            "not_run_no_key": _m(agg["nokey"], "requests not sent because FIRMS_MAP_KEY is missing", has_jobs),
            "failed_days": _m(agg["dfail"], "days whose FIRMS request failed after retries", has_jobs),
            "recent_problems": errors,
        },
        "notes": ["'Not available' means the value cannot be determined from the stored data; it is not zero.",
                  "Real NASA and synthetic rows are always counted separately.",
                  "Coordinates, dates and times are validated before storage, so invalid values appear under rejected rows, not in stored rows."],
    }
