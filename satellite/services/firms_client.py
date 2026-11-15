"""NASA FIRMS API client (server side only).

Endpoints used (documented at https://firms.modaps.eosdis.nasa.gov/api/):
  /api/area/csv/{MAP_KEY}/{SOURCE}/{west,south,east,north}/{DAY_RANGE}/{YYYY-MM-DD}
        DAY_RANGE is 1..5 days, returns [DATE .. DATE+DAY_RANGE-1]
  /api/data_availability/csv/{MAP_KEY}/{SOURCE|all}
  /mapserver/mapkey_status/?MAP_KEY={MAP_KEY}     (transaction budget)

The MAP_KEY travels in the URL path, so every message built here goes through
``_redact`` and the key is never logged or stored.
"""

from __future__ import annotations

import csv
import io
import logging
import time
from datetime import date, timedelta

import requests
from django.conf import settings

from .products import PRODUCTS

log = logging.getLogger(__name__)
MAX_DAY_RANGE = 5


class FirmsError(Exception):
    """Base error; messages are safe to show to users."""


class MissingKey(FirmsError):
    pass


class InvalidKey(FirmsError):
    pass


class RateLimited(FirmsError):
    pass


class FirmsUnavailable(FirmsError):
    pass


class BadResponse(FirmsError):
    pass


def _redact(text, key):
    return str(text).replace(key, "***") if key else str(text)


class FirmsClient:
    def __init__(self, map_key=None, base_url=None, timeout=None, session=None, retries=2, backoff=2.0, sleep=time.sleep):
        self.key = (map_key if map_key is not None else getattr(settings, "FIRMS_MAP_KEY", "")).strip()
        self.base = (base_url or getattr(settings, "FIRMS_BASE_URL", "https://firms.modaps.eosdis.nasa.gov")).rstrip("/")
        self.timeout = timeout or getattr(settings, "FIRMS_TIMEOUT_SECONDS", 60)
        self.session = session or requests.Session()
        self.retries, self.backoff, self._sleep = retries, backoff, sleep
        self.requests_made = 0

    @property
    def configured(self):
        return bool(self.key)

    def _require_key(self):
        if not self.key:
            raise MissingKey("No NASA FIRMS MAP_KEY is configured. Set the FIRMS_MAP_KEY environment variable "
                             "(free key: https://firms.modaps.eosdis.nasa.gov/api/map_key).")

    def _get(self, path):
        url = f"{self.base}{path}"
        last = None
        for attempt in range(self.retries + 1):
            try:
                self.requests_made += 1
                resp = self.session.get(url, timeout=self.timeout)
            except requests.Timeout:
                last = FirmsUnavailable("NASA FIRMS did not answer in time.")
            except requests.RequestException as exc:
                last = FirmsUnavailable(f"Could not reach NASA FIRMS: {_redact(exc.__class__.__name__, self.key)}.")
            else:
                if resp.status_code in (401, 403):
                    raise InvalidKey("NASA FIRMS rejected the MAP_KEY (HTTP %s)." % resp.status_code)
                if resp.status_code == 429:
                    raise RateLimited("NASA FIRMS transaction limit reached (5000 per 10 minutes). Wait and retry.")
                if resp.status_code >= 500:
                    last = FirmsUnavailable(f"NASA FIRMS server error (HTTP {resp.status_code}).")
                elif resp.status_code != 200:
                    raise BadResponse(f"Unexpected HTTP {resp.status_code} from NASA FIRMS.")
                else:
                    return resp.text
            if attempt < self.retries:
                self._sleep(self.backoff * (2 ** attempt))
        raise last

    # ------------------------------------------------------------- key status
    def key_status(self):
        """Returns the mapkey_status JSON (transaction_limit, current_transactions, ...)."""
        self._require_key()
        text = self._get(f"/mapserver/mapkey_status/?MAP_KEY={self.key}")
        try:
            import json
            data = json.loads(text)
        except ValueError:
            raise InvalidKey("NASA FIRMS did not recognise the MAP_KEY.")
        if not isinstance(data, dict) or "transaction_limit" not in data:
            raise InvalidKey("NASA FIRMS did not recognise the MAP_KEY.")
        return data

    # ------------------------------------------------------------ availability
    def availability(self, source="all"):
        """{data_id: (min_date, max_date)} from /api/data_availability."""
        self._require_key()
        text = self._get(f"/api/data_availability/csv/{self.key}/{source}")
        out = {}
        for row in csv.DictReader(io.StringIO(text)):
            try:
                out[row["data_id"]] = (date.fromisoformat(row["min_date"]), date.fromisoformat(row["max_date"]))
            except (KeyError, ValueError, TypeError):
                continue
        if not out and text.strip():
            raise BadResponse("Could not read the FIRMS data availability table.")
        return out

    # -------------------------------------------------------------------- area
    def fetch_area(self, source, bbox, start, days):
        """One request: rows (dicts) for [start, start+days-1]. bbox = (south, west, north, east)."""
        self._require_key()
        if source not in PRODUCTS:
            raise ValueError(f"Unknown FIRMS source '{source}'.")
        if not 1 <= days <= MAX_DAY_RANGE:
            raise ValueError("DAY_RANGE must be between 1 and 5.")
        south, west, north, east = bbox
        coords = f"{west:g},{south:g},{east:g},{north:g}"  # FIRMS order: west,south,east,north
        text = self._get(f"/api/area/csv/{self.key}/{source}/{coords}/{days}/{start.isoformat()}")
        return parse_csv_text(text)

    def iter_days(self, source, bbox, start, end):
        """Yield (chunk_start, chunk_days, rows_or_exception) for each <=5 day chunk."""
        d = start
        while d <= end:
            n = min(MAX_DAY_RANGE, (end - d).days + 1)
            try:
                yield d, n, self.fetch_area(source, bbox, d, n)
            except (InvalidKey, MissingKey, RateLimited):
                raise
            except FirmsError as exc:
                yield d, n, exc
            d += timedelta(days=n)


def parse_csv_text(text):
    """FIRMS answers errors as plain text with HTTP 200; a real answer starts with the 'latitude' header."""
    body = (text or "").strip()
    if not body:
        return []
    first = body.splitlines()[0].lower()
    if "latitude" not in first:
        low = body[:200].lower()
        if "invalid" in low and "key" in low:
            raise InvalidKey("NASA FIRMS rejected the MAP_KEY.")
        if "exceed" in low or "limit" in low:
            raise RateLimited("NASA FIRMS transaction limit reached. Wait 10 minutes and retry.")
        raise BadResponse("NASA FIRMS returned an unexpected (non-CSV) answer.")
    return list(csv.DictReader(io.StringIO(body)))
