"""Overview, data-quality and MODIS-vs-VIIRS harmonization statistics."""

from __future__ import annotations

from collections import Counter, defaultdict
from statistics import median

from django.db.models import Count, Max, Min, Q

from satellite.harmonization.pipeline import DEFAULT_PARAMS, find_overlaps, haversine_km, obs_minutes
from satellite.models import FetchJob, HarmonizationRun, Hotspot
from satellite.analysis.params import data_mode, hotspots
from satellite.services.products import PRODUCTS


def overview(filters):
    qs = hotspots(**filters)
    by_sensor = {r["sensor"]: r["n"] for r in qs.values("sensor").annotate(n=Count("id"))}
    by_platform = Counter()
    for sat, sensor, n in qs.values_list("satellite", "sensor").annotate(n=Count("id")).values_list("satellite", "sensor", "n"):
        by_platform[(sensor, Hotspot.PLATFORMS.get(sat.strip().lower(), sat or "unknown"))] += n
    span = qs.aggregate(first=Min("acq_date"), last=Max("acq_date"))
    cats = Counter()
    for demo, prod, n in qs.values_list("is_demo", "product").annotate(n=Count("id")).values_list("is_demo", "product", "n"):
        row = Hotspot(is_demo=demo, product=prod)
        cats[row.category] += n
    last_job = (FetchJob.objects.filter(status__in=(FetchJob.Status.SUCCESS, FetchJob.Status.PARTIAL))
                .exclude(kind=FetchJob.Kind.DEMO).order_by("-finished_at").first())
    products = [{"id": p, "label": PRODUCTS[p].label, "n": n} for p, n in
                qs.exclude(product="").values_list("product").annotate(n=Count("id")).order_by("-n")]
    return {
        "scope": filters["scope"], "data_mode": data_mode(), "aoi": dict(zip(("south", "west", "north", "east"), filters["bbox"])),
        "period": {"start": filters["start"] or span["first"], "end": filters["end"] or span["last"]},
        "total": sum(by_sensor.values()), "modis": by_sensor.get("MODIS", 0), "viirs": by_sensor.get("VIIRS", 0),
        "platforms": [{"sensor": s, "platform": p, "n": n} for (s, p), n in sorted(by_platform.items())],
        "categories": dict(cats), "products": products, "data_first": span["first"], "data_last": span["last"],
        "last_update": last_job.finished_at if last_job else None,
        "source": "NASA FIRMS (Fire Information for Resource Management System), active-fire products "
                  "MCD14DL (MODIS) and VNP14IMG/VJ114IMG/VJ214IMG (VIIRS 375 m)",
        "quality": quality(qs),
    }


def quality(qs):
    total = qs.count()
    if not total:
        return {"records": 0}
    agg = qs.aggregate(
        no_conf=Count("id", filter=Q(confidence="")), no_frp=Count("id", filter=Q(frp__isnull=True)),
        no_bright=Count("id", filter=Q(brightness__isnull=True)), low=Count("id", filter=Q(confidence="l")),
        overlap=Count("id", filter=Q(overlap_group__isnull=False)), unprocessed_legacy=Count("id", filter=Q(product="")))
    sup = Hotspot.objects.filter(exact_dup_of__isnull=False, is_demo=qs.filter(is_demo=True).exists()).count()
    jobs = FetchJob.objects.exclude(kind=FetchJob.Kind.DEMO).aggregate(inv=Count("id", filter=Q(rows_invalid__gt=0)))
    return {"records": total, "missing_confidence": agg["no_conf"], "missing_frp": agg["no_frp"],
            "missing_brightness": agg["no_bright"], "low_confidence": agg["low"],
            "in_overlap_groups": agg["overlap"], "exact_duplicates_flagged": sup,
            "rows_without_product_metadata": agg["unprocessed_legacy"],
            "imports_with_rejected_rows": jobs["inv"],
            "rejected_rows": sum(FetchJob.objects.exclude(kind=FetchJob.Kind.DEMO).values_list("rows_invalid", flat=True))}


def _stats(vals):
    vals = sorted(v for v in vals if v is not None)
    if not vals:
        return None
    n = len(vals)
    return {"n": n, "median": round(median(vals), 2), "p10": round(vals[int(.1 * (n - 1))], 2),
            "p90": round(vals[int(.9 * (n - 1))], 2), "max": round(vals[-1], 2)}


def _conf_counts(sq):
    out = {"low": 0, "nominal": 0, "high": 0, "not_provided": 0}
    names = {"l": "low", "n": "nominal", "h": "high", "": "not_provided"}
    for c, n in sq.values_list("confidence").annotate(n=Count("id")).values_list("confidence", "n"):
        out[names.get(c, "not_provided")] += n
    return out


SENSOR_NOTES = [
    "MODIS (Terra, Aqua) detects thermal anomalies in pixels of about 1 km at nadir (up to about 4.8 km at the scan edge); VIIRS (Suomi-NPP, NOAA-20, NOAA-21) uses 375 m pixels. VIIRS can therefore see smaller or cooler fires that MODIS cannot, and a higher VIIRS count does not mean VIIRS 'detects more fires'.",
    "Each satellite passes at fixed local times (Terra about 10:30, Aqua and Suomi-NPP about 13:30, NOAA-20 about 13:10 local, plus night passes). A fire that ignites or burns out between passes is seen by one sensor only.",
    "Confidence is defined differently: MODIS gives a 0-100 percentage, VIIRS gives low/nominal/high. The classes are filtered separately for each sensor and never converted into one another.",
    "FRP and brightness temperature depend on pixel size and spectral channel (MODIS T21/T31, VIIRS I-4/I-5); they are shown side by side, never averaged or merged.",
    "A detection with no counterpart in the other sensor is NOT evidence of a missed or false detection: it may be outside the other sensor's swath or time window, under cloud, or below its sensitivity.",
    "A hotspot is a satellite thermal anomaly, not a confirmed wildfire. Matching does not validate detections; no independent reference data is used.",
]
MATCH_ROW_LIMIT = 60000

PARAMETER_STATUS = {
    "kind": "heuristic, configurable",
    "validated": False,
    "statement": "These matching parameters are analyst-chosen heuristics. They have not been validated against independent reference data "
                 "and are not universal scientific thresholds. Change them and compare the results before drawing conclusions.",
    "time_window": "Default 180 min. It admits near-simultaneous passes (Aqua / Suomi-NPP, NOAA-20) and also the roughly 3 h gap between Terra "
                   "(about 10:30) and the afternoon satellites. A pair 3 h apart means 'activity at the same place on the same day', not 'the same "
                   "detection event': fires change within hours, and a recurring or persistent heat source can link unrelated detections. "
                   "Use 30-60 min to keep only near-coincident passes.",
    "radius": "The distance criterion is the mean of the two pixel sizes (about 0.7 km at nadir, up to about 2.8 km at the MODIS scan edge). "
              "The 6 km value is only a safety cap against corrupt pixel sizes and normally never binds. Geolocation error, pixel overlap geometry "
              "and clustered fires mean a match is a plausible co-location, not proof of the same fire.",
}


def matching_summary(qs, params=None):
    """Live MODIS/VIIRS matching on the selected records (does not write anything, repeatable).

    Parameters (scientifically bounded): max_minutes 10-720 (default 180), max_radius_km 0.5-10 (default 6).
    The distance criterion is footprint overlap: centre distance <= (pixel_modis + pixel_viirs) / 2.
    """
    p = {**DEFAULT_PARAMS, **(params or {})}
    if qs.filter(is_demo=True).exists() and qs.filter(is_demo=False).exists():
        return {"available": False, "reason": "The selection mixes real NASA and synthetic rows. Matching is only computed within one category."}
    rows = list(qs.order_by().values_list("id", "sensor", "latitude", "longitude", "acq_date", "acq_time", "scan", "track")[:MATCH_ROW_LIMIT + 1])
    if len(rows) > MATCH_ROW_LIMIT:
        return {"available": False, "reason": f"More than {MATCH_ROW_LIMIT} detections selected; narrow the area or period to compute matching."}
    data = [(i, s, la, lo, obs_minutes(d, t), sc, tr) for i, s, la, lo, d, t, sc, tr in rows]
    groups, pairs = find_overlaps(data, p)
    n_m = sum(1 for r in data if r[1] == "MODIS")
    n_v = len(data) - n_m
    m_match = sum(1 for r in data if r[1] == "MODIS" and r[0] in groups)
    v_match = sum(1 for r in data if r[1] == "VIIRS" and r[0] in groups)
    def pixel(sensor, sc, tr):
        v = max([x for x in (sc, tr) if x] or [p["default_pixel_km"][sensor]])
        return v
    pm = [pixel("MODIS", r[5], r[6]) for r in data if r[1] == "MODIS"]
    pv = [pixel("VIIRS", r[5], r[6]) for r in data if r[1] == "VIIRS"]
    radius = None
    if pm and pv:
        radius = {"min": round((min(pm) + min(pv)) / 2, 2), "max": round(min(p["max_radius_km"], (max(pm) + max(pv)) / 2), 2)}
    return {"available": True, "input_modis": n_m, "input_viirs": n_v, "input_total": len(data),
            "matched_modis": m_match, "matched_viirs": v_match, "unmatched_modis": n_m - m_match, "unmatched_viirs": n_v - v_match,
            "match_groups": len(set(groups.values())), "pairs_linked": pairs,
            "parameter_status": PARAMETER_STATUS,
            "effective_radius_km": radius,
            "parameters": {"max_minutes": p["max_minutes"], "max_radius_km": p["max_radius_km"],
                           "pixel_fallback_km": p["default_pixel_km"], "distance_rule": "centre distance <= mean of the two pixel sizes"},
            "reading": "'Matched' = a detection of the other sensor lies inside the same footprint and time window. "
                       "'Unmatched' only says no such neighbour was found in the selected area and period (edge effects "
                       "apply at the area border); it does not mean a missed detection or a false alarm."}


def harmonization_stats(filters, params=None):
    """Sensor-specific statistics side by side, duplicate and overlap analysis. No merging of measurements."""
    qs = hotspots(**filters)
    sensors = {}
    for s in ("MODIS", "VIIRS"):
        sq = qs.filter(sensor=s)
        sensors[s] = {"n": sq.count(),
                      "frp_mw": _stats(sq.values_list("frp", flat=True)),
                      "brightness_k": _stats(sq.values_list("brightness", flat=True)),
                      "brightness_2_k": _stats(sq.values_list("brightness_2", flat=True)),
                      "pixel_km": _stats([max(a, b) for a, b in sq.exclude(scan__isnull=True).exclude(track__isnull=True)
                                          .values_list("scan", "track")]),
                      "confidence": _conf_counts(sq),
                      "day_night": {k: sq.filter(daynight=k).count() for k in ("D", "N")}}
    dup_filters = {**filters, "include_superseded": True}
    all_rows = hotspots(**dup_filters)
    dup = all_rows.filter(exact_dup_of__isnull=False)
    dup_by_product = {p or "(legacy)": n for p, n in dup.values_list("product").annotate(n=Count("id")).values_list("product", "n")}
    groups = defaultdict(list)
    for gid, sensor, lat, lon, day, t, frp in qs.filter(overlap_group__isnull=False).values_list(
            "overlap_group", "sensor", "latitude", "longitude", "acq_date", "acq_time", "frp").iterator(chunk_size=5000):
        groups[gid].append((sensor, lat, lon, obs_minutes(day, t), frp))
    dts, ratios, comp = [], [], 0
    for members in groups.values():
        m = [x for x in members if x[0] == "MODIS"]
        v = [x for x in members if x[0] == "VIIRS"]
        if not m or not v:
            continue
        mind = min(abs(a[3] - b[3]) for a in m for b in v)
        dts.append(mind)
        comp += mind <= DEFAULT_PARAMS["coincident_minutes"]
        if len(m) == 1 and m[0][4] and all(x[4] is not None for x in v):
            ratios.append(sum(x[4] for x in v) / m[0][4])
    n_overlap_m = qs.filter(sensor="MODIS", overlap_group__isnull=False).count()
    n_overlap_v = qs.filter(sensor="VIIRS", overlap_group__isnull=False).count()
    return {
        "sensors": sensors, "unified_total": sensors["MODIS"]["n"] + sensors["VIIRS"]["n"],
        "exact_duplicates": {"flagged": dup.count(), "by_product": dup_by_product,
                             "rule": "same sensor, platform, latitude, longitude, UTC date and time in different products; "
                                     "preferred copy: SP > manual CSV > NRT. Flagged rows are kept in the database, excluded from counts."},
        "overlap": {"groups": len(groups), "modis_records_in_groups": n_overlap_m, "viirs_records_in_groups": n_overlap_v,
                    "share_modis_with_viirs_neighbour": round(n_overlap_m / sensors["MODIS"]["n"], 3) if sensors["MODIS"]["n"] else None,
                    "share_viirs_with_modis_neighbour": round(n_overlap_v / sensors["VIIRS"]["n"], 3) if sensors["VIIRS"]["n"] else None,
                    "coincident_groups_within_minutes": {"minutes": DEFAULT_PARAMS["coincident_minutes"], "groups": comp},
                    "median_time_difference_min": round(median(dts), 1) if dts else None,
                    "viirs_to_modis_frp_ratio": _stats(ratios),
                    "parameters": {k: DEFAULT_PARAMS[k] for k in ("max_minutes", "max_radius_km")},
                    "parameters_status": "heuristic defaults used by the stored harmonization run; configurable, not validated",
                    "note": "The FRP ratio (sum of VIIRS FRP / MODIS FRP) is descriptive, for groups with one MODIS "
                            "record. It is NOT a calibration; the two FRP values are never averaged or merged."},
        "matching": matching_summary(qs, params),
        "sensor_notes": SENSOR_NOTES,
        "input_summary": {"total_rows_selected_category": all_rows.count(), "active_unique": sensors["MODIS"]["n"] + sensors["VIIRS"]["n"],
                          "exact_duplicates_flagged_not_deleted": dup.count()},
        "last_run": _last_run(filters["scope"]),
    }


def _last_run(scope):
    r = HarmonizationRun.objects.filter(scope=scope).first()
    return None if not r else {"status": r.status, "finished_at": r.finished_at, "start": r.start_date,
                               "end": r.end_date, "stats": r.stats}
