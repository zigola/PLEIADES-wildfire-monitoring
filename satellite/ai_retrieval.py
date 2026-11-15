"""Question-driven retrieval of POLARIS database facts for the AI assistant.

For every question this module decides which database queries are relevant, runs them live and
returns the results with their provenance (query name, data category, time of retrieval).
Nothing is cached and nothing is a hardcoded sample: counts, latest observations, regional
changes, quality and harmonization figures all come from the database at question time.
Static scientific background lives in BACKGROUND and is always labelled as such.
"""

from __future__ import annotations

import re
import math
from datetime import date, timedelta

from django.db.models import Count, Max, Min
from django.utils import timezone

from .analysis import coverage as cov
from .analysis.overview import SENSOR_NOTES, harmonization_stats, quality
from .analysis.params import DEFAULT_BBOX, REGIONS, hotspots
from .analysis.quality import quality_report
from .models import FetchJob, HarmonizationRun, Hotspot

BACKGROUND = SENSOR_NOTES + [
    "Anomaly scores are relative unusualness indicators within the analysed data, not probabilities or forecasts.",
    "Missing records do not prove that no fire occurred: coverage, clouds, overpass timing and product availability matter.",
    "FRP (MW) is the radiative power of a pixel, not fire size, burned area or total energy.",
]

KEYWORDS = {
    "latest": r"\b(latest|recent|newest|last|dernier|derni[eè]re|r[eé]cent)",
    "count": r"\b(how many|count|number|total|combien|nombre)",
    "diff": r"\b(differ|difference|compare|comparison|versus|vs|diff[eé]rence|comparer)",
    "region": r"\b(region|regions|where|which area|increase|increased|rising|hotspot area|r[eé]gion|o[uù]|hausse|augment)",
    "limits": r"\b(limit|limitation|caveat|uncertain|reliab|limite|fiabilit)",
    "quality": r"\b(quality|missing|duplicate|invalid|error|import|qualit[eé]|manquant|doublon|erreur)",
    "harm": r"\b(harmoni|match|overlap|unmatched|recouvr|appari)",
    "synthetic": r"\b(synthetic|demo|demonstration|fake|simulated|synth[eé]tique|d[eé]mo|r[eé]el|real|nasa)",
}
MONTHS = {m: i + 1 for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"])}


def detect(question):
    q = question.lower()
    intents = {k for k, rx in KEYWORDS.items() if re.search(rx, q)}
    sensors = [s for s in ("MODIS", "VIIRS") if s.lower() in q] or ["MODIS", "VIIRS"]
    regions = [k for k, (name, _b) in REGIONS.items() if k != "morocco" and (k in q or name.split(" (")[0].lower().split(" / ")[0] in q)]
    dates = []
    for m in re.finditer(r"\b(20\d{2}|19\d{2})-(\d{2})-(\d{2})\b", q):
        try:
            dates.append(date(int(m.group(1)), int(m.group(2)), int(m.group(3))))
        except ValueError:
            pass
    month_year = re.search(r"\b(" + "|".join(MONTHS) + r")\s+(20\d{2}|19\d{2})\b", q)
    year = re.search(r"\b(20\d{2}|19\d{2})\b", q)
    french = bool(re.search(r"\b(combien|quelle|quelles|quel|les|des|est-ce|donn[eé]es|régions?|derni[eè]res?|observations? r[eé]elles?)\b", q))
    return {"intents": intents, "sensors": sensors, "regions": regions, "dates": dates,
            "month_year": (MONTHS[month_year.group(1)], int(month_year.group(2))) if month_year else None,
            "year": int(year.group(1)) if year else None, "french": french}


def _counts(qs):
    return {r["sensor"]: r["n"] for r in qs.values("sensor").annotate(n=Count("id"))}


def base_counts():
    all_qs = Hotspot.objects.all()
    real = all_qs.filter(is_demo=False, exact_dup_of__isnull=True)
    demo = all_qs.filter(is_demo=True, exact_dup_of__isnull=True)
    last_job = FetchJob.objects.filter(kind=FetchJob.Kind.API, status__in=("success", "partial")).order_by("-finished_at").first()
    span = lambda qs: qs.aggregate(a=Min("acq_date"), b=Max("acq_date"))
    return {
        "real_nasa": {"total": real.count(), "by_sensor": _counts(real), "dates": span(real)},
        "synthetic_demo": {"total": demo.count(), "by_sensor": _counts(demo), "dates": span(demo),
                           "label": "generated demonstration data, NOT NASA observations"},
        "flagged_duplicate_rows": all_qs.filter(exact_dup_of__isnull=False).count(),
        "last_successful_nasa_api_import": {"finished_at": last_job.finished_at, "product": last_job.product,
                                            "period": [last_job.start_date, last_job.end_date]} if last_job else None,
    }


def _scope_for(plan, base):
    """Real data unless the question is about demo data or there is no real data."""
    if base["real_nasa"]["total"] == 0 and base["synthetic_demo"]["total"] > 0:
        return "demo"
    return "demo" if re.search(r"synthetic|demo|d[eé]mo|synth", plan.get("_q", "")) else "real"


def latest_observations(scope, sensor, n=10):
    qs = hotspots(scope=scope, sensors=[sensor], confidences=None).order_by("-acq_date", "-acq_time", "-id")[:n]
    rows = []
    for h in qs:
        rows.append({"id": h.id, "sensor": h.sensor, "satellite": h.platform, "date": h.acq_date, "time_utc": h.acq_hhmm,
                     "lat": h.latitude, "lon": h.longitude, "frp_mw": h.frp, "confidence_raw": h.confidence_raw or None,
                     "daynight": h.daynight or None, "product": h.product or None, "category": h.category})
    return rows


def period_counts(scope, sensors, start, end, bbox=DEFAULT_BBOX):
    qs = hotspots(bbox=bbox, scope=scope, start=start, end=end, sensors=sensors, confidences=None)
    cnt = _counts(qs)
    ranges = cov.coverage_ranges(bbox, scope)
    covered = {s: bool(cov.covered_days(ranges.get(s, []), start, end)) for s in sensors}
    return {"start": start, "end": end, "bbox_s_w_n_e": bbox, "counts_by_sensor": cnt, "total": sum(cnt.values()),
            "sensor_has_any_import_coverage_in_period": covered,
            "note": "A zero count is only a true zero where import coverage exists; otherwise the period was not observed in POLARIS."}


def regional_changes(scope, window_days=14):
    """Per named region: detections in the latest window vs the window before it (relative to the newest stored date)."""
    last = hotspots(scope=scope).aggregate(m=Max("acq_date"))["m"]
    if not last:
        return {"available": False, "reason": f"No {scope} detections stored."}
    r0, r1 = last - timedelta(days=window_days - 1), last
    p0, p1 = r0 - timedelta(days=window_days), r0 - timedelta(days=1)
    out = []
    for key, (name, bbox) in REGIONS.items():
        if key == "morocco":
            continue
        row = {"region": name, "bbox_s_w_n_e": bbox}
        complete = True
        for sensor in ("MODIS", "VIIRS"):
            now = hotspots(bbox=bbox, scope=scope, start=r0, end=r1, sensors=[sensor], confidences=None).count()
            before = hotspots(bbox=bbox, scope=scope, start=p0, end=p1, sensors=[sensor], confidences=None).count()
            ranges = cov.coverage_ranges(bbox, scope).get(sensor, [])
            ok = len(cov.covered_days(ranges, p0, r1)) == (r1 - p0).days + 1
            complete = complete and (ok or (now + before) > 0)
            row[sensor.lower()] = {"recent": now, "previous": before,
                                   "ratio": round(now / before, 2) if before else None, "full_import_coverage": ok}
        out.append(row)
    out.sort(key=lambda r: -(r["modis"]["recent"] + r["viirs"]["recent"]))
    return {"available": True, "recent_window": [r0, r1], "previous_window": [p0, p1], "regions": out,
            "method": f"count of detections in the latest {window_days} stored days vs the {window_days} days before, per sensor; "
                      "descriptive only (no significance test), sensors never added together",
            "caveat": "A rise can reflect seasonality, more overpasses or a coverage difference; it is not a fire forecast."}


def day_status(scope, day):
    from .analysis.calendar import build_calendar
    from .analysis.params import common_filters
    f = common_filters({"scope": scope, "min_conf": "all"})
    d = build_calendar(f, day, day)["days"][0]
    return {"date": day, "status": d["status"], "modis": d["modis"], "viirs": d["viirs"], "observed_by": d["covered"],
            "meaning": {"no_data": "this day was never imported for the Morocco area: unknown, not zero",
                        "zero": "imported and no detection", "detections": "detections stored"}[d["status"]]}


def _iot_evidence_snapshot(include_satellite_comparison=False):
    """Compact live IoT and incident evidence for POLARIS AI; unknown values stay null."""
    from iot.models import Station
    from iot.models import Reading
    from alerts.models import IncidentReport
    from django.db.models import Count
    now = timezone.now()
    today = timezone.localdate()
    stations = []
    for station in Station.objects.filter(is_active=True)[:30]:
        reading = Reading.objects.filter(station=station).first()
        online = bool(reading and reading.received_at >= now - timedelta(minutes=10))
        latest = None
        if reading:
            latest = {"received_at": reading.received_at, "risk_level": reading.risk_level,
                "temperature_c": reading.temperature_c, "humidity_pct": reading.humidity_pct, "pressure_hpa": reading.pressure_hpa,
                "pm1_ugm3": reading.pm1_ugm3, "pm25_ugm3": reading.pm25_ugm3, "pm10_ugm3": reading.pm10_ugm3,
                "gas_adc": reading.gas_adc, "gas_voltage_mv": reading.gas_voltage_mv,
                "smoke_index": reading.smoke_index, "battery_voltage": reading.battery_voltage,
                "solar_voltage": reading.solar_voltage, "solar_current_ma": reading.solar_current_ma,
                "solar_power_mw": reading.solar_power_mw, "rssi_dbm": reading.rssi,
                "lora_snr_db": reading.lora_snr, "lora_frequency_mhz": reading.lora_frequency_mhz,
                "lora_spreading_factor": reading.lora_spreading_factor, "packet_loss_pct": reading.packet_loss_pct}
        nearby_satellite = None
        if include_satellite_comparison and station.latitude is not None and station.longitude is not None and len(stations) < 10:
            lat_delta = 10 / 111.0
            lon_delta = 10 / max(20.0, 111.0 * math.cos(math.radians(station.latitude)))
            bbox = (station.latitude-lat_delta, station.longitude-lon_delta,
                    station.latitude+lat_delta, station.longitude+lon_delta)
            nearby = hotspots(bbox=bbox, scope="real", start=today-timedelta(days=6), end=today,
                              sensors=["MODIS", "VIIRS"], confidences=None)
            nearby_satellite = {row["sensor"]: row["n"] for row in nearby.values("sensor").annotate(n=Count("id"))}
        stations.append({"station_id": station.station_id, "name": station.name, "is_simulated": station.is_simulated,
            "data_type": "SIMULATED" if station.is_simulated else "DEVICE_TELEMETRY", "latitude": station.latitude, "longitude": station.longitude,
            "connection": "ONLINE" if online else "OFFLINE_OR_NO_RECENT_PACKET", "latest": latest,
            "nearby_real_satellite_detections_last_7_days": nearby_satellite})
    status_counts = {row["status"]: row["n"] for row in IncidentReport.objects.values("status").annotate(n=Count("id"))}
    return {"retrieved_at": now, "station_count": Station.objects.filter(is_active=True).count(),
        "stations_included_limit_30": stations, "incident_status_counts": status_counts,
        "total_incidents": IncidentReport.objects.count(),
        "recent_incidents": [{"id": item.id, "title": item.title, "status": item.status, "source": item.source,
            "station": item.station.name if item.station else None, "latitude": item.latitude, "longitude": item.longitude,
            "created_at": item.created_at, "message": item.message, "evidence": item.evidence}
            for item in IncidentReport.objects.select_related("station").order_by("-created_at")[:10]],
        "satellite_comparison_note": "Nearby MODIS/VIIRS counts are real NASA records already imported into POLARIS within an approximate 10 km bounding box and the last 7 days. They are independent from IoT packets and are not one-to-one matches.",
        "interpretation_limits": ["A packet arriving recently does not prove sensor calibration or hardware health.",
            "Missing values are unknown, not zero.", "IoT threshold alerts are screening indicators requiring human verification.",
            "LoRa RSSI and SNR are link metrics; they do not prove end-to-end service quality."]}


def retrieve(question):
    plan = detect(question)
    plan["_q"] = question.lower()
    base = base_counts()
    scope = _scope_for(plan, base)
    queries = [{"name": "base_counts", "category": "database", "note": "live counts of real NASA and synthetic rows"}]
    results = {"base_counts": base, "scope_used_for_details": scope, "retrieved_at": timezone.now()}
    it = plan["intents"]

    def add(name, value, note):
        results[name] = value
        queries.append({"name": name, "category": f"database:{scope}", "note": note})

    if "latest" in it or not it:
        add("latest_observations", {s: latest_observations(scope, s) for s in plan["sensors"]}, "10 newest stored detections per sensor")
    if plan["dates"]:
        add("day_status", [day_status(scope, d) for d in plan["dates"][:3]], "calendar status of the requested day(s)")
        add("period_counts", [period_counts(scope, plan["sensors"], d, d) for d in plan["dates"][:3]], "detections on the requested day(s)")
    elif plan["month_year"]:
        m, y = plan["month_year"]
        start = date(y, m, 1)
        end = (date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1))
        add("period_counts", period_counts(scope, plan["sensors"], start, end), "detections in the requested month")
    elif plan["year"] and ("count" in it or "region" in it or "diff" in it or "synthetic" not in it):
        add("period_counts", period_counts(scope, plan["sensors"], date(plan["year"], 1, 1), date(plan["year"], 12, 31)), "detections in the requested year")
    if "region" in it or plan["regions"]:
        add("regional_changes", regional_changes(scope), "latest vs previous window per named region")
    if it & {"diff", "harm", "limits"}:
        bbox = REGIONS[plan["regions"][0]][1] if plan["regions"] else DEFAULT_BBOX
        from .analysis.params import common_filters
        f = common_filters({"scope": scope, "min_conf": "all", **dict(zip(("south", "west", "north", "east"), map(str, bbox)))})
        try:
            h = harmonization_stats(f)
            h.pop("sensor_notes", None)
            add("harmonization", h, "sensor statistics, duplicates, live MODIS/VIIRS matching")
        except Exception:
            results["harmonization"] = {"available": False, "reason": "could not be computed"}
    if "quality" in it:
        add("data_quality", quality_report(), "data quality report (unavailable values are null, not zero)")
    # IoT telemetry is always included in the compact AI context so cross-module questions
    # can relate field readings, satellite records and pending verification reports.
    wants_cross_source = bool(re.search(r"\b(compare|comparison|versus|vs|satellite|modis|viirs|comparaison|comparer|قارن|مقارنة)\b", question.lower()))
    results["iot_network"] = _iot_evidence_snapshot(include_satellite_comparison=wants_cross_source)
    queries.append({"name": "iot_network", "category": "database:iot", "note": "live IoT station telemetry and incident workflow; latest values only"})
    run = HarmonizationRun.objects.filter(status="success").first()
    if run and "harm" in it:
        add("last_harmonization_run", {"scope": run.scope, "period": [run.start_date, run.end_date], "stats": run.stats, "parameters": run.parameters}, "persisted run")
    return {"plan": {"intents": sorted(it), "sensors": plan["sensors"], "regions": plan["regions"], "french": plan["french"]},
            "queries": queries, "results": results, "background": BACKGROUND}


# ------------------------------------------------------------------ database-only answer (no LLM)
_FR = {"title": "Réponse issue de la base de données POLARIS (sans modèle de langage)", "real": "Observations NASA réelles",
       "synth": "Données synthétiques de démonstration (PAS des observations NASA)", "none": "aucune", "last": "Dernier import NASA réussi",
       "never": "aucun import NASA réussi", "latest": "Dernières observations", "bg": "Contexte scientifique (général, pas un résultat POLARIS)",
       "src": "Sources", "recent": "récent", "prev": "précédent", "region": "Régions (fenêtre récente vs précédente)"}
_EN = {"title": "Answer from the POLARIS database (no language model)", "real": "Real NASA observations",
       "synth": "Synthetic demonstration data (NOT NASA observations)", "none": "none", "last": "Last successful NASA import",
       "never": "no successful NASA import yet", "latest": "Latest observations", "bg": "Scientific background (general, not a POLARIS result)",
       "src": "Sources", "recent": "recent", "prev": "previous", "region": "Regions (latest vs previous window)"}


def database_only_answer(retrieval):
    T = _FR if retrieval["plan"]["french"] else _EN
    r, b = retrieval["results"], retrieval["results"]["base_counts"]
    lines = [T["title"] + ".", ""]
    for key, label in (("real_nasa", T["real"]), ("synthetic_demo", T["synth"])):
        c = b[key]
        d = c["dates"]
        span = f"{d['a']} → {d['b']}" if d["a"] else T["none"]
        lines.append(f"- [DB] {label}: {c['total']} ({', '.join(f'{k} {v}' for k, v in c['by_sensor'].items()) or T['none']}); {span}")
    li = b["last_successful_nasa_api_import"]
    lines.append(f"- [DB] {T['last']}: " + (f"{li['product']} ({li['finished_at']:%Y-%m-%d %H:%M} UTC)" if li and li["finished_at"] else T["never"]))
    if "latest_observations" in r:
        lines += ["", f"{T['latest']} ({r['scope_used_for_details']}):"]
        for sensor, rows in r["latest_observations"].items():
            for o in rows[:5]:
                lines.append(f"- [DB] {sensor} {o['satellite']} {o['date']} {o['time_utc']} UTC, {o['lat']:.3f}, {o['lon']:.3f}, FRP {o['frp_mw']}")
    iot = r.get("iot_network", {})
    if iot:
        lines += ["", "IoT field network:"]
        lines.append(f"- [DB] Active stations: {iot.get('station_count', 0)}; recorded incident reports: {iot.get('total_incidents', 0)}")
        for station in iot.get("stations_included_limit_30", [])[:8]:
            latest = station.get("latest") or {}
            lines.append(f"- [DB] {station.get('name') or station.get('station_id')} ({station.get('data_type')}): {station.get('connection')}; "
                         f"latest risk {latest.get('risk_level', 'unknown')}; temperature {latest.get('temperature_c', 'unknown')} °C; "
                         f"humidity {latest.get('humidity_pct', 'unknown')}%; pressure {latest.get('pressure_hpa', 'unknown')} hPa; "
                         f"PM2.5 {latest.get('pm25_ugm3', 'unknown')} µg/m³; gas ADC {latest.get('gas_adc', 'unknown')}; "
                         f"battery {latest.get('battery_voltage', 'unknown')} V; solar {latest.get('solar_voltage', 'unknown')} V; "
                         f"LoRa RSSI {latest.get('rssi_dbm', 'unknown')} dBm and SNR {latest.get('lora_snr_db', 'unknown')} dB")
            nearby = station.get("nearby_real_satellite_detections_last_7_days")
            if nearby is not None:
                lines.append(f"- [DB] Nearby real NASA records in approximate 10 km / 7 days: MODIS {nearby.get('MODIS', 0)}, VIIRS {nearby.get('VIIRS', 0)}; independent observations, not matched events.")
        lines.append(f"- [DB] Incident statuses: {iot.get('incident_status_counts', {})}")
        for incident in iot.get("recent_incidents", [])[:5]:
            lines.append(f"- [DB] Incident #{incident.get('id')}: {incident.get('status')} / {incident.get('source')} / "
                         f"{incident.get('station') or 'unknown station'} — {incident.get('title')}")
        lines.append("[Interpretation] IoT alerts are screening signals and require human verification; missing values are unknown, not zero.")
    rc = r.get("regional_changes")
    if rc and rc.get("available"):
        lines += ["", T["region"] + ":"]
        for x in rc["regions"][:6]:
            lines.append(f"- [DB] {x['region']}: MODIS {x['modis']['recent']} / {x['modis']['previous']}, VIIRS {x['viirs']['recent']} / {x['viirs']['previous']}")
        lines.append("[Interpretation] " + rc["caveat"])
    h = r.get("harmonization")
    if h and h.get("matching", {}).get("available"):
        m = h["matching"]
        lines += ["", f"- [DB] MODIS {m['input_modis']}, VIIRS {m['input_viirs']}; matched {m['matched_modis']}/{m['matched_viirs']}; unmatched {m['unmatched_modis']}/{m['unmatched_viirs']}"]
    lines += ["", T["bg"] + ":"] + [f"- {x}" for x in retrieval["background"][:4]]
    lines += ["", T["src"] + ": " + ", ".join(q["name"] for q in retrieval["queries"])]
    return "\n".join(lines)
