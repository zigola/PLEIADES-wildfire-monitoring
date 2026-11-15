"""Request parameter parsing shared by every satellite JSON endpoint."""

from __future__ import annotations

from datetime import date, timedelta

from django.db.models import Q

from satellite.models import Hotspot

DEFAULT_BBOX = (20.84, -17.06, 35.74, -1.54)  # south, west, north, east (Morocco + Western Sahara)
CONFIDENCE_LEVELS = {"all": ["l", "n", "h", ""], "l": ["l", "n", "h"], "n": ["n", "h"], "h": ["h"]}
SENSORS = ("MODIS", "VIIRS")


class BadRequest(ValueError):
    pass


def _float(get, name, default):
    raw = get.get(name)
    if raw in (None, ""):
        return default
    try:
        value = float(raw)
    except ValueError:
        raise BadRequest(f"'{name}' must be a number.")
    if value != value or value in (float("inf"), float("-inf")):
        raise BadRequest(f"'{name}' must be a finite number.")
    return value


def parse_bbox(get):
    south = _float(get, "south", DEFAULT_BBOX[0])
    west = _float(get, "west", DEFAULT_BBOX[1])
    north = _float(get, "north", DEFAULT_BBOX[2])
    east = _float(get, "east", DEFAULT_BBOX[3])
    if not (-90 <= south < north <= 90):
        raise BadRequest("Latitudes must satisfy -90 <= south < north <= 90.")
    if not (-180 <= west < east <= 180):
        raise BadRequest("Longitudes must satisfy -180 <= west < east <= 180.")
    return south, west, north, east


def parse_conf(get):
    """Confidence filter per sensor: {"MODIS": [...classes], "VIIRS": [...classes]}.

    MODIS confidence is a 0-100 percentage (classes l <30, n 30-79, h >= 80) and VIIRS confidence is
    a categorical l/n/h. They are different scales, so the filter is applied to each sensor separately
    and never as one shared numeric threshold. ``min_conf`` sets both; ``modis_conf`` / ``viirs_conf``
    override one sensor.
    """
    base = get.get("min_conf", "n")
    out = {}
    for sensor, key in (("MODIS", "modis_conf"), ("VIIRS", "viirs_conf")):
        level = get.get(key) or base
        if level not in CONFIDENCE_LEVELS:
            raise BadRequest(f"'{key if get.get(key) else 'min_conf'}' must be one of all, l, n, h.")
        out[sensor] = CONFIDENCE_LEVELS[level]
    return out


def parse_date(get, name, default=None):
    raw = get.get(name)
    if not raw:
        return default
    try:
        return date.fromisoformat(raw)
    except ValueError:
        raise BadRequest(f"'{name}' must be a date like 2025-07-31.")


def parse_sensors(get):
    raw = get.get("sensor", "")
    if not raw:
        return list(SENSORS)
    out = [s.strip().upper() for s in raw.split(",") if s.strip()]
    if not out or any(s not in SENSORS for s in out):
        raise BadRequest("'sensor' must be MODIS, VIIRS or MODIS,VIIRS.")
    return out


def parse_int(get, name, default, lo, hi):
    raw = get.get(name)
    if raw in (None, ""):
        return default
    try:
        v = int(raw)
    except ValueError:
        raise BadRequest(f"'{name}' must be an integer.")
    if not lo <= v <= hi:
        raise BadRequest(f"'{name}' must be between {lo} and {hi}.")
    return v


def parse_float_range(get, name, default, lo, hi):
    v = _float(get, name, default)
    if not lo <= v <= hi:
        raise BadRequest(f"'{name}' must be between {lo} and {hi}.")
    return v


def data_modes():
    demo = Hotspot.objects.filter(is_demo=True).exists()
    real = Hotspot.objects.filter(is_demo=False).exists()
    return real, demo


def data_mode():
    real, demo = data_modes()
    if real and demo:
        return "mixed"
    return "demo" if demo else ("real" if real else "empty")


def resolve_scope(get):
    """'real' | 'demo' | 'all' | 'empty'.  Default: real NASA data if any exists, otherwise demo.

    Synthetic rows are NEVER silently merged with real ones: 'all' must be asked for explicitly.
    """
    real, demo = data_modes()
    wanted = get.get("scope", "")
    if wanted not in ("", "real", "demo", "all"):
        raise BadRequest("'scope' must be real, demo or all.")
    if wanted:
        return wanted
    if real:
        return "real"
    return "demo" if demo else "empty"


def hotspots(bbox=None, scope="real", start=None, end=None, sensors=None, confidences=None,
             satellites=None, daynight=None, include_superseded=False):
    qs = Hotspot.objects.all()
    if scope == "real":
        qs = qs.filter(is_demo=False)
    elif scope == "demo":
        qs = qs.filter(is_demo=True)
    elif scope == "empty":
        return qs.none()
    if bbox:
        s, w, n, e = bbox
        qs = qs.filter(latitude__gte=s, latitude__lte=n, longitude__gte=w, longitude__lte=e)
    if start:
        qs = qs.filter(acq_date__gte=start)
    if end:
        qs = qs.filter(acq_date__lte=end)
    if sensors and set(sensors) != set(SENSORS):
        qs = qs.filter(sensor__in=sensors)
    if isinstance(confidences, dict):
        q = Q()
        for sensor, levels in confidences.items():
            q |= Q(sensor=sensor, confidence__in=levels)
        qs = qs.filter(q)
    elif confidences is not None:
        qs = qs.filter(confidence__in=confidences)
    if satellites:
        qs = qs.filter(satellite__in=satellites)
    if daynight in ("D", "N"):
        qs = qs.filter(daynight=daynight)
    if not include_superseded:
        qs = qs.filter(exact_dup_of__isnull=True)
    return qs


def common_filters(get):
    """Parse the filters every endpoint shares. Returns a dict of keyword arguments for ``hotspots``."""
    scope = resolve_scope(get)
    start, end = parse_date(get, "start"), parse_date(get, "end")
    if start and end and start > end:
        raise BadRequest("'start' must not be after 'end'.")
    dn = get.get("daynight", "").upper()
    if dn not in ("", "D", "N"):
        raise BadRequest("'daynight' must be D or N.")
    sats = [s for s in get.get("satellite", "").split(",") if s]
    return dict(bbox=parse_bbox(get), scope=scope, start=start, end=end, sensors=parse_sensors(get),
                confidences=parse_conf(get), satellites=sats or None, daynight=dn or None)


# Named areas shared by the AI assistant and documentation (same boxes as the interface presets).
REGIONS = {
    "morocco": ("Morocco & Western Sahara", DEFAULT_BBOX),
    "north": ("Northern Morocco (Rif, Gharb)", (33.8, -6.8, 35.9, -3.4)),
    "atlas": ("Middle & High Atlas", (31.0, -8.0, 33.8, -3.5)),
    "tadla": ("Tadla / Beni Mellal", (32.0, -7.2, 33.0, -5.6)),
    "oriental": ("Oriental", (33.0, -3.5, 35.3, -1.2)),
    "souss": ("Souss-Massa", (29.5, -10.2, 31.0, -8.0)),
}


def require_single_category(filters):
    """Derived analyses (calendar index, anomalies, clusters, matching, comparison) must never blend real NASA
    and synthetic rows. Plain listings, counts and exports may use scope=all because they are labelled per row."""
    if filters["scope"] == "all":
        raise BadRequest("This analysis is not available for 'both' categories: real NASA and synthetic data are never "
                         "combined in derived results. Choose NASA observations or Synthetic demo.")
    return filters
