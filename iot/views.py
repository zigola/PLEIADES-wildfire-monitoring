import hmac
import json
import math
from datetime import timedelta

from django.db.models import Prefetch
from django.http import JsonResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from accounts.decorators import operator_required
from accounts.roles import user_role, OPERATOR
from alerts.models import IncidentReport
from .models import Station, Reading


NUMERIC_RANGES = {
    "temperature_c": (-60, 100), "humidity_pct": (0, 100), "pressure_hpa": (300, 1200),
    "pm1_ugm3": (0, 5000), "pm25_ugm3": (0, 5000), "pm10_ugm3": (0, 5000), "gas_adc": (0, 65535),
    "gas_voltage_mv": (0, 10000), "wind_speed_kmh": (0, 300), "smoke_index": (0, 1000),
    "battery_voltage": (0, 100), "solar_voltage": (0, 100), "solar_current_ma": (0, 100000),
    "solar_power_mw": (0, 10000000), "rssi": (-160, 20), "lora_snr": (-50, 50),
    "lora_frequency_mhz": (100, 1000), "lora_spreading_factor": (5, 12), "packet_loss_pct": (0, 100),
}
TEXT_FIELDS = {"wind_direction": 16}
INTEGER_FIELDS = {"gas_adc", "gas_voltage_mv", "rssi", "lora_spreading_factor"}


def _authorized(request):
    from django.conf import settings
    import os
    expected = getattr(settings, "POLARIS_IOT_API_KEY", "") or os.environ.get("POLARIS_IOT_API_KEY", "")
    supplied = request.headers.get("X-API-Key", "")
    # constant-time comparison: do not leak how many leading characters of the key were right
    return bool(expected) and hmac.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8"))


def _clean_payload(payload):
    if not isinstance(payload, dict):
        raise ValueError("The JSON body must be an object.")
    cleaned = {}
    for key, (low, high) in NUMERIC_RANGES.items():
        if key not in payload or payload[key] is None or payload[key] == "":
            continue
        value = payload[key]
        if isinstance(value, bool):
            raise ValueError(f"{key} must be numeric.")
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise ValueError(f"{key} must be numeric.")
        if not math.isfinite(number) or not low <= number <= high:
            raise ValueError(f"{key} must be between {low} and {high}.")
        if key in INTEGER_FIELDS and not number.is_integer():
            raise ValueError(f"{key} must be an integer.")
        cleaned[key] = int(number) if key in INTEGER_FIELDS else number
    for key, limit in TEXT_FIELDS.items():
        if key in payload:
            cleaned[key] = str(payload[key])[:limit]
    return cleaned


def _risk(data):
    """Conservative screening only; this is not a certified fire detector."""
    temp, gas, smoke = data.get("temperature_c"), data.get("gas_adc"), data.get("smoke_index")
    gas_high = gas is not None and gas >= 2600
    smoke_high = smoke is not None and smoke >= 60
    temp_high = temp is not None and temp >= 55
    temp_elevated = temp is not None and temp >= 40
    if (temp_high and (gas_high or smoke_high)) or (gas_high and smoke_high and temp_elevated):
        return "POTENTIAL_FIRE"
    if temp_high or gas_high or smoke_high:
        return "WARNING"
    if temp_elevated:
        return "WATCH"
    return "NORMAL"


def _register_auto_incident(station, reading):
    if reading.risk_level != "POTENTIAL_FIRE":
        return None
    recent = IncidentReport.objects.filter(station=station, source=IncidentReport.Source.IOT,
                created_at__gte=timezone.now()-timedelta(minutes=20)).first()
    if recent:
        return recent.id
    report = IncidentReport.objects.create(
        station=station, source=IncidentReport.Source.IOT, title=f"Potential fire indicator · {station.name or station.station_id}",
        message="Multiple sensor indicators crossed the configured screening thresholds. Operator verification is required before any public or emergency warning.",
        latitude=station.latitude, longitude=station.longitude,
        evidence={"reading_id": reading.id, "risk_level": reading.risk_level, "temperature_c": reading.temperature_c,
                  "gas_adc": reading.gas_adc, "smoke_index": reading.smoke_index, "received_at": reading.received_at.isoformat()})
    return report.id


def _reading_dict(r):
    return {
        "id": r.id, "temperature_c": r.temperature_c, "humidity_pct": r.humidity_pct, "pressure_hpa": r.pressure_hpa,
        "pm1_ugm3": r.pm1_ugm3, "pm25_ugm3": r.pm25_ugm3, "pm10_ugm3": r.pm10_ugm3,
        "gas_adc": r.gas_adc, "gas_voltage_mv": r.gas_voltage_mv, "wind_speed_kmh": r.wind_speed_kmh,
        "wind_direction": r.wind_direction, "smoke_index": r.smoke_index,
        "battery_voltage": r.battery_voltage, "solar_voltage": r.solar_voltage,
        "solar_current_ma": r.solar_current_ma, "solar_power_mw": r.solar_power_mw,
        "rssi": r.rssi, "lora_snr": r.lora_snr, "lora_frequency_mhz": r.lora_frequency_mhz,
        "lora_spreading_factor": r.lora_spreading_factor, "packet_loss_pct": r.packet_loss_pct,
        "risk_level": r.risk_level, "received_at": r.received_at.isoformat(),
    }


@csrf_exempt
@require_POST
def ingest(request):
    if not _authorized(request):
        return HttpResponseForbidden("Invalid or missing X-API-Key")
    try:
        payload = json.loads(request.body or b"{}")
        if not isinstance(payload, dict):
            raise ValueError("The JSON body must be an object.")
        sid = str(payload.get("station_id", "")).strip()[:64]
        if not sid:
            return JsonResponse({"error": "station_id is required"}, status=400)
        cleaned = _clean_payload(payload)
        lat, lon = payload.get("latitude"), payload.get("longitude")
        if lat not in (None, "") and not -90 <= float(lat) <= 90:
            raise ValueError("latitude must be between -90 and 90.")
        if lon not in (None, "") and not -180 <= float(lon) <= 180:
            raise ValueError("longitude must be between -180 and 180.")
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        return JsonResponse({"error": str(exc) or "Invalid JSON"}, status=400)

    station, _ = Station.objects.get_or_create(station_id=sid, defaults={"name": str(payload.get("station_name", sid))[:120]})
    update_fields = []
    if payload.get("station_name"):
        station.name = str(payload["station_name"])[:120]
        update_fields.append("name")
    if lat not in (None, "") and lon not in (None, ""):
        station.latitude, station.longitude = float(lat), float(lon)
        update_fields += ["latitude", "longitude"]
    if station.is_simulated:
        station.is_simulated = False
        update_fields.append("is_simulated")
    if not station.is_active:
        station.is_active = True
        update_fields.append("is_active")
    if update_fields:
        station.save(update_fields=update_fields)

    cleaned["risk_level"] = _risk(cleaned)
    reading = Reading.objects.create(station=station, **cleaned)
    incident_id = _register_auto_incident(station, reading)
    return JsonResponse({"ok": True, "reading_id": reading.id, "risk_level": reading.risk_level,
                         "verification_required": reading.risk_level == "POTENTIAL_FIRE", "incident_id": incident_id}, status=201)


@require_GET
def latest(request):
    stations = []
    cutoff = timezone.now() - timedelta(minutes=10)
    latest_reading = Prefetch("readings", queryset=Reading.objects.order_by("-received_at", "-id")[:1], to_attr="latest_reading")
    for station in Station.objects.filter(is_active=True).prefetch_related(latest_reading):
        reading = station.latest_reading[0] if station.latest_reading else None
        online = bool(reading and reading.received_at >= cutoff)
        stations.append({"station_id": station.station_id, "name": station.name or station.station_id,
            "latitude": station.latitude, "longitude": station.longitude, "is_simulated": station.is_simulated, "online": online,
            "connection_state": "ONLINE" if online else "OFFLINE",
            "latest": _reading_dict(reading) if reading else None})
    return JsonResponse({"stations": stations, "server_time": timezone.now().isoformat(),
                         "data_note": "No reading means unknown, not zero. ONLINE means a reading arrived within 10 minutes."})


@require_GET
def history(request, station_id):
    if not request.user.is_authenticated:
        return HttpResponseForbidden()
    rows = Reading.objects.filter(station__station_id=station_id)[:200]
    return JsonResponse({"readings": [_reading_dict(r) for r in rows]})


@require_GET
def incidents(request):
    """Incident feed.

    Automatic screening reports and field reports are unverified until an operator reviews them, so only
    operators receive them (with message and evidence). Everyone else sees confirmed / resolved incidents
    without free text or raw evidence, plus the number of reports still awaiting verification.
    """
    operator = user_role(request.user) == OPERATOR
    qs = IncidentReport.objects.select_related("station")
    pending_count = qs.filter(status=IncidentReport.Status.PENDING).count()
    if not operator:
        qs = qs.filter(status__in=(IncidentReport.Status.CONFIRMED, IncidentReport.Status.RESOLVED))
    items = []
    for x in qs[:100]:
        item = {"id": x.id, "title": x.title, "source": x.source, "status": x.status,
                "station": x.station.name if x.station else None,
                "station_id": x.station.station_id if x.station else None, "latitude": x.latitude, "longitude": x.longitude,
                "created_at": x.created_at.isoformat(), "reviewed_at": x.reviewed_at.isoformat() if x.reviewed_at else None}
        if operator:
            item["message"], item["evidence"] = x.message, x.evidence
        items.append(item)
    return JsonResponse({"incidents": items, "pending_count": pending_count, "visibility": "operator" if operator else "public",
                         "note": None if operator else "Unverified reports are visible to operators only."})


@operator_required
@require_POST
def report_fire(request):
    try:
        payload = json.loads(request.body or b"{}")
        if not isinstance(payload, dict):
            raise ValueError
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return JsonResponse({"error": "Invalid JSON object."}, status=400)
    message = str(payload.get("message", "")).strip()
    if len(message) < 8 or len(message) > 2000:
        return JsonResponse({"error": "Describe the observation in 8–2000 characters."}, status=400)
    station = None
    if payload.get("station_id"):
        station = get_object_or_404(Station, station_id=str(payload["station_id"])[:64])
    lat, lon = payload.get("latitude"), payload.get("longitude")
    try:
        lat = float(lat) if lat not in (None, "") else (station.latitude if station else None)
        lon = float(lon) if lon not in (None, "") else (station.longitude if station else None)
        if lat is not None and not -90 <= lat <= 90: raise ValueError
        if lon is not None and not -180 <= lon <= 180: raise ValueError
    except (ValueError, TypeError):
        return JsonResponse({"error": "Invalid coordinates."}, status=400)
    incident = IncidentReport.objects.create(station=station, source=IncidentReport.Source.MANUAL,
        title="Manual fire report", message=message, latitude=lat, longitude=lon, reporter=request.user)
    return JsonResponse({"ok": True, "incident_id": incident.id, "status": incident.status,
                         "message": "Report recorded and awaiting operator verification."}, status=201)


@operator_required
@require_POST
def review_incident(request, incident_id):
    incident = get_object_or_404(IncidentReport, pk=incident_id)
    try:
        payload = json.loads(request.body or b"{}")
        if not isinstance(payload, dict):
            raise ValueError
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return JsonResponse({"error": "Invalid JSON object."}, status=400)
    action = payload.get("action")
    states = {"confirm": IncidentReport.Status.CONFIRMED, "reject": IncidentReport.Status.REJECTED,
              "resolve": IncidentReport.Status.RESOLVED}
    if action not in states:
        return JsonResponse({"error": "Action must be confirm, reject or resolve."}, status=400)
    allowed = {IncidentReport.Status.PENDING: {"confirm", "reject"},
               IncidentReport.Status.CONFIRMED: {"resolve"}}
    if action not in allowed.get(incident.status, set()):
        return JsonResponse({"error": "This incident cannot make that status transition."}, status=409)
    incident.status = states[action]
    incident.reviewed_at = timezone.now()
    incident.reviewed_by = request.user
    incident.save(update_fields=["status", "reviewed_at", "reviewed_by"])
    return JsonResponse({"ok": True, "status": incident.status})


@require_GET
def comparison(request):
    """Side-by-side independent IoT and NASA satellite observations around one geolocated station."""
    from datetime import date
    from django.db.models import Count, Max, Avg
    from satellite.models import Hotspot
    from satellite.analysis.params import hotspots
    station_id = request.GET.get("station", "").strip()
    station = get_object_or_404(Station, station_id=station_id) if station_id else Station.objects.filter(is_active=True).first()
    if not station:
        return JsonResponse({"available": False, "reason": "No IoT station is registered yet."})
    if station.latitude is None or station.longitude is None:
        return JsonResponse({"available": False, "reason": "This station has no GPS coordinates; add coordinates to compare nearby satellite observations."})
    try:
        days = int(request.GET.get("days", "7"))
        radius_km = float(request.GET.get("radius_km", "10"))
        if days not in (1, 3, 7, 14, 30) or not 1 <= radius_km <= 50: raise ValueError
    except ValueError:
        return JsonResponse({"error": "days must be 1, 3, 7, 14 or 30; radius_km must be 1–50."}, status=400)
    end = timezone.localdate()
    start = end - timedelta(days=days - 1)
    lat_delta = radius_km / 111.0
    lon_delta = radius_km / max(20.0, 111.0 * math.cos(math.radians(station.latitude)))
    bbox = (station.latitude-lat_delta, station.longitude-lon_delta, station.latitude+lat_delta, station.longitude+lon_delta)
    qs = hotspots(bbox=bbox, scope="real", start=start, end=end, sensors=["MODIS", "VIIRS"], confidences=None)
    # The bounding box narrows the database query; haversine then enforces a true circular radius.
    satellite_counts = {"MODIS": 0, "VIIRS": 0}
    latest_observation = None
    station_phi = math.radians(station.latitude)
    for sensor, lat, lon, acquired in qs.values_list("sensor", "latitude", "longitude", "acq_date").iterator(chunk_size=2000):
        phi1, phi2 = station_phi, math.radians(lat)
        d_phi = phi2 - phi1
        d_lambda = math.radians(lon - station.longitude)
        a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
        distance_km = 6371.0088 * 2 * math.asin(min(1.0, math.sqrt(a)))
        if distance_km <= radius_km:
            satellite_counts[sensor] = satellite_counts.get(sensor, 0) + 1
            if latest_observation is None or acquired > latest_observation:
                latest_observation = acquired
    satellite = {"source": "NASA FIRMS records already imported into POLARIS", "scope": "real NASA records only",
        "start": start.isoformat(), "end": end.isoformat(), "radius_km": radius_km, "spatial_filter": "circular haversine radius",
        "modis": satellite_counts.get("MODIS", 0), "viirs": satellite_counts.get("VIIRS", 0), "total": sum(satellite_counts.values()),
        "latest_observation": latest_observation}
    readings = Reading.objects.filter(station=station, received_at__date__gte=start, received_at__date__lte=end)
    aggregate = readings.aggregate(n=Count("id"), temp=Avg("temperature_c"), humidity=Avg("humidity_pct"), pressure=Avg("pressure_hpa"),
                                    pm25=Avg("pm25_ugm3"), gas=Avg("gas_adc"), smoke=Avg("smoke_index"), solar=Avg("solar_voltage"), battery=Avg("battery_voltage"))
    iot = {"readings": aggregate["n"], "temperature_avg_c": round(aggregate["temp"], 2) if aggregate["temp"] is not None else None,
        "humidity_avg_pct": round(aggregate["humidity"], 2) if aggregate["humidity"] is not None else None,
        "pressure_avg_hpa": round(aggregate["pressure"], 2) if aggregate["pressure"] is not None else None,
        "pm25_avg_ugm3": round(aggregate["pm25"], 2) if aggregate["pm25"] is not None else None,
        "gas_adc_avg": round(aggregate["gas"], 1) if aggregate["gas"] is not None else None,
        "smoke_index_avg": round(aggregate["smoke"], 1) if aggregate["smoke"] is not None else None,
        "solar_voltage_avg": round(aggregate["solar"], 2) if aggregate["solar"] is not None else None,
        "battery_voltage_avg": round(aggregate["battery"], 2) if aggregate["battery"] is not None else None,
        "latest_reading": readings.order_by("-received_at").values_list("received_at", flat=True).first()}
    return JsonResponse({"available": True, "station": {"id": station.station_id, "name": station.name or station.station_id, "is_simulated": station.is_simulated,
        "latitude": station.latitude, "longitude": station.longitude}, "period_days": days, "iot": iot, "satellite": satellite,
        "iot_data_type": "SIMULATED" if station.is_simulated else "DEVICE_TELEMETRY",
        "method_note": "These are independent observing systems. Satellite hotspot counts are not sensor readings and do not confirm a fire. No one-to-one match is implied. Zero imported detections does not prove there was no fire: data coverage, clouds and satellite overpass timing matter."}, json_dumps_params={"default": str})


def dashboard(request):
    return render(request, "iot/dashboard.html", {"is_operator": user_role(request.user) == OPERATOR,
        "stations": Station.objects.filter(is_active=True).order_by("name", "station_id")})


def comparison_page(request):
    return render(request, "iot/comparison.html", {"stations": Station.objects.filter(is_active=True).order_by("name", "station_id")})
