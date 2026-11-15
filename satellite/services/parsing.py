"""Validation and normalization of one FIRMS row (MODIS or VIIRS) into Hotspot fields.

Normalization rules (documented in the methodology page):
  * field names: MODIS brightness/bright_t31 and VIIRS bright_ti4/bright_ti5 are stored in
    ``brightness`` / ``brightness_2``. They are different spectral channels and are NEVER mixed.
  * timestamps: acq_date + acq_time are UTC (HHMM, not zero padded by the API).
  * confidence: kept as delivered in ``confidence_raw``; ``confidence`` is a derived class.
  * the sensor and the original satellite code are preserved.
  * missing optional values stay NULL, they are never replaced by zeros or averages.
"""

from __future__ import annotations

import math
from datetime import date

from .products import MODIS, VIIRS, PRODUCTS

VIIRS_CONF = {"l": "l", "low": "l", "n": "n", "nominal": "n", "h": "h", "high": "h"}

REASONS = {
    "missing_coordinate": "latitude/longitude missing or not numeric",
    "coordinate_out_of_range": "latitude outside -90..90 or longitude outside -180..180",
    "bad_date": "acq_date missing or not YYYY-MM-DD",
    "bad_time": "acq_time missing or not a valid HHMM",
    "negative_frp": "frp is negative",
    "bad_number": "a numeric measurement is not a finite number",
}


class RecordError(ValueError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _num(value, nullable=True):
    value = (value if not isinstance(value, str) else value.strip())
    if value in (None, ""):
        if nullable:
            return None
        raise RecordError("missing_coordinate")
    try:
        x = float(value)
    except (TypeError, ValueError):
        raise RecordError("bad_number")
    if not math.isfinite(x):
        raise RecordError("bad_number")
    return x


def detect_sensor(fieldnames):
    names = {(f or "").strip().lower() for f in fieldnames or []}
    if "bright_ti4" in names:
        return VIIRS
    if "brightness" in names and "bright_t31" in names:
        return MODIS
    return None


def classify_confidence(raw, sensor):
    """Return (derived class, raw string). Empty class = not provided / unreadable."""
    raw = "" if raw is None else str(raw).strip()
    if raw == "":
        return "", ""
    if sensor == VIIRS:
        return VIIRS_CONF.get(raw.lower(), ""), raw
    try:  # MODIS: percentage
        pct = float(raw)
    except ValueError:
        return VIIRS_CONF.get(raw.lower(), ""), raw
    if not 0 <= pct <= 100:
        return "", raw
    return ("l" if pct < 30 else "h" if pct >= 80 else "n"), raw


def parse_time(value):
    try:
        t = int(float(str(value).strip()))
    except (TypeError, ValueError):
        raise RecordError("bad_time")
    if not (0 <= t <= 2359) or t % 100 > 59:
        raise RecordError("bad_time")
    return t


def normalize_row(row, sensor, product=""):
    """dict (FIRMS CSV row) -> kwargs for Hotspot. Raises RecordError with a reason code."""
    if sensor not in (MODIS, VIIRS):
        raise ValueError("sensor must be MODIS or VIIRS")
    row = {(k or "").strip().lower(): v for k, v in row.items()}
    try:
        lat, lon = _num(row.get("latitude"), nullable=False), _num(row.get("longitude"), nullable=False)
    except RecordError as exc:
        if exc.code == "bad_number":
            raise RecordError("missing_coordinate")
        raise
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise RecordError("coordinate_out_of_range")
    try:
        day = date.fromisoformat((row.get("acq_date") or "").strip())
    except ValueError:
        raise RecordError("bad_date")
    acq_time = parse_time(row.get("acq_time", ""))
    frp = _num(row.get("frp"))
    if frp is not None and frp < 0:
        raise RecordError("negative_frp")
    b1, b2 = (("brightness", "bright_t31") if sensor == MODIS else ("bright_ti4", "bright_ti5"))
    conf, conf_raw = classify_confidence(row.get("confidence"), sensor)
    return {
        "sensor": sensor,
        "satellite": (row.get("satellite") or "").strip()[:10],
        "instrument": (row.get("instrument") or sensor).strip()[:10],
        "product": product,
        "version": (row.get("version") or "").strip()[:12],
        "latitude": lat, "longitude": lon,
        "acq_date": day, "acq_time": acq_time,
        "frp": frp,
        "brightness": _num(row.get(b1)), "brightness_2": _num(row.get(b2)),
        "scan": _num(row.get("scan")), "track": _num(row.get("track")),
        "confidence": conf, "confidence_raw": conf_raw[:8],
        "daynight": (row.get("daynight") or "").strip()[:1].upper(),
    }


def sensor_for_product(product):
    return PRODUCTS[product].sensor
