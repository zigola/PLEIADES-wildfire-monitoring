from django.conf import settings
from django.db import models
from django.db.models import Q


class Hotspot(models.Model):
    """One active-fire detection (a MODIS or VIIRS pixel) from NASA FIRMS."""

    class Sensor(models.TextChoices):
        MODIS = "MODIS", "MODIS (about 1 km)"
        VIIRS = "VIIRS", "VIIRS (about 375 m)"

    class Confidence(models.TextChoices):
        UNKNOWN = "", "Not provided"
        LOW = "l", "Low"
        NOMINAL = "n", "Nominal"
        HIGH = "h", "High"

    sensor = models.CharField(max_length=5, choices=Sensor.choices)
    satellite = models.CharField(max_length=10, blank=True)
    latitude = models.FloatField()
    longitude = models.FloatField()
    acq_date = models.DateField()
    acq_time = models.PositiveSmallIntegerField(default=0, help_text="UTC, HHMM")
    frp = models.FloatField(null=True, blank=True, help_text="Fire radiative power, MW")
    confidence = models.CharField(max_length=1, blank=True, choices=Confidence.choices, default=Confidence.NOMINAL,
                                  help_text="Derived class (l/n/h; empty = not provided). MODIS 0-29/30-79/80-100; "
                                            "VIIRS as delivered. Classes of the two sensors are not equivalent.")
    daynight = models.CharField(max_length=1, blank=True)
    is_demo = models.BooleanField(default=False, help_text="Synthetic row, not real fire data")
    source = models.CharField(max_length=120, blank=True)

    # ---- provenance and sensor-specific measurements (added in 0002) -------------
    instrument = models.CharField(max_length=10, blank=True, help_text="Instrument column of the FIRMS product")
    product = models.CharField(max_length=24, blank=True, db_index=True,
                               help_text="FIRMS source id, e.g. MODIS_SP, VIIRS_SNPP_NRT. Empty for legacy rows")
    version = models.CharField(max_length=12, blank=True, help_text="Algorithm collection, e.g. 6.1NRT, 2.0NRT")
    brightness = models.FloatField(null=True, blank=True,
                                   help_text="MODIS brightness T21 / VIIRS bright_ti4, Kelvin (sensor-specific, not comparable)")
    brightness_2 = models.FloatField(null=True, blank=True,
                                     help_text="MODIS bright_t31 / VIIRS bright_ti5, Kelvin")
    scan = models.FloatField(null=True, blank=True, help_text="Along-scan pixel size, km")
    track = models.FloatField(null=True, blank=True, help_text="Along-track pixel size, km")
    confidence_raw = models.CharField(max_length=8, blank=True,
                                      help_text="Confidence exactly as delivered (MODIS 0-100, VIIRS l/n/h)")
    fetch = models.ForeignKey("FetchJob", null=True, blank=True, on_delete=models.SET_NULL,
                              related_name="hotspots", help_text="Import that created this row")

    # ---- harmonization annotations (written by satellite.harmonization.pipeline) --
    exact_dup_of = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL,
                                     related_name="superseded", db_index=True,
                                     help_text="Set when this row is the same observation as another row "
                                               "(e.g. NRT copy of an SP record). Kept, never deleted.")
    overlap_group = models.BigIntegerField(null=True, blank=True, db_index=True,
                                           help_text="Shared id of MODIS and VIIRS detections that plausibly "
                                                     "describe the same heat source")

    class Meta:
        ordering = ["acq_date", "acq_time"]
        indexes = [
            models.Index(fields=["sensor", "acq_date"], name="hotspot_sensor_date_idx"),
            models.Index(fields=["latitude", "longitude"], name="hotspot_latlon_idx"),
            models.Index(fields=["is_demo", "acq_date"], name="hotspot_demo_date_idx"),
            models.Index(fields=["acq_date", "latitude", "longitude"], name="hotspot_date_latlon_idx"),
            models.Index(fields=["satellite", "acq_date"], name="hotspot_sat_date_idx"),
        ]
        constraints = [
            # Real rows: the same product cannot hold the same observation twice.
            models.UniqueConstraint(
                fields=["product", "satellite", "latitude", "longitude", "acq_date", "acq_time"],
                condition=Q(is_demo=False) & ~Q(product=""),
                name="hotspot_unique_real_observation",
            ),
        ]

    # canonical platform names; the raw FIRMS code stays in ``satellite``
    PLATFORMS = {"terra": "Terra", "aqua": "Aqua", "n": "Suomi-NPP", "1": "Suomi-NPP", "snpp": "Suomi-NPP",
                 "n20": "NOAA-20", "noaa20": "NOAA-20", "n21": "NOAA-21", "noaa21": "NOAA-21"}

    @property
    def platform(self):
        return self.PLATFORMS.get(self.satellite.strip().lower(), self.satellite or "unknown")

    @property
    def acq_hhmm(self):
        return f"{self.acq_time // 100:02d}:{self.acq_time % 100:02d}"

    @property
    def category(self):
        """The data category shown to users; never mixes demo with NASA data."""
        if self.is_demo:
            return "synthetic_demo"
        return "nasa_nrt" if self.product.endswith("_NRT") else (
            "nasa_standard" if self.product.endswith("_SP") else "nasa_imported_csv")

    def __str__(self):
        return f"{self.sensor} {self.acq_date} ({self.latitude:.3f}, {self.longitude:.3f})"



class FetchJob(models.Model):
    """One import (API request series, CSV file or demo generation) with its metadata.

    This is the dataset-level provenance record: source, product, area, period,
    counts and quality information survive even if rows are later filtered.
    """

    class Kind(models.TextChoices):
        API = "api", "NASA FIRMS API"
        CSV = "csv", "NASA FIRMS CSV file"
        DEMO = "demo", "Synthetic demonstration data"

    class Status(models.TextChoices):
        RUNNING = "running", "Running"
        SUCCESS = "success", "Completed"
        PARTIAL = "partial", "Completed with errors"
        FAILED = "failed", "Failed"
        NO_KEY = "no_key", "Not run: no API key"

    kind = models.CharField(max_length=5, choices=Kind.choices)
    product = models.CharField(max_length=24, blank=True, help_text="FIRMS source id, or 'MIXED'")
    sensor = models.CharField(max_length=5, blank=True)
    south = models.FloatField(null=True, blank=True)
    west = models.FloatField(null=True, blank=True)
    north = models.FloatField(null=True, blank=True)
    east = models.FloatField(null=True, blank=True)
    coverage_declared = models.BooleanField(
        default=True, help_text="False when the area/period was inferred from the file content, not declared")
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.RUNNING)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    source_label = models.CharField(max_length=200, blank=True, help_text="Endpoint family or file name; never a key")
    requests_made = models.PositiveIntegerField(default=0)
    days_ok = models.PositiveIntegerField(default=0)
    days_failed = models.PositiveIntegerField(default=0)
    rows_received = models.PositiveIntegerField(default=0)
    rows_created = models.PositiveIntegerField(default=0)
    rows_duplicate = models.PositiveIntegerField(default=0, help_text="Already stored (same product and observation)")
    rows_invalid = models.PositiveIntegerField(default=0)
    invalid_reasons = models.JSONField(default=dict, blank=True)
    message = models.TextField(blank=True)
    availability = models.JSONField(default=dict, blank=True, help_text="FIRMS data_availability snapshot")
    covered_ranges = models.JSONField(default=list, blank=True,
                                      help_text="[[start, end], ...] date ranges the area was successfully queried for")

    class Meta:
        ordering = ["-started_at"]

    def __str__(self):
        return f"{self.get_kind_display()} {self.product} {self.start_date}..{self.end_date} [{self.status}]"

    @property
    def has_bbox(self):
        return None not in (self.south, self.west, self.north, self.east)


class HarmonizationRun(models.Model):
    """Processing record of one pass of the duplicate / overlap pipeline."""

    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=8, default="running")
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    scope = models.CharField(max_length=8, default="real", help_text="real or demo; the two are never mixed")
    parameters = models.JSONField(default=dict, blank=True)
    stats = models.JSONField(default=dict, blank=True)
    message = models.TextField(blank=True)

    class Meta:
        ordering = ["-started_at"]


class SyncRun(models.Model):
    """One scheduled check of NASA FIRMS (every ``FIRMS_SYNC_INTERVAL_SECONDS``, default 10 minutes).

    Records when the check ran, what was requested, what NASA answered and what was new. It is a log, not data:
    detections themselves are stored as ``Hotspot`` rows. Checking every 10 minutes does not mean NASA publishes
    every 10 minutes: ``newest_observation`` is the newest acquisition time actually stored for the area.
    """

    class Status(models.TextChoices):
        RUNNING = "running", "Running"
        OK = "ok", "Completed"
        PARTIAL = "partial", "Completed with errors"
        FAILED = "failed", "Failed"
        RATE_LIMITED = "rate_limited", "NASA rate limit reached"
        NO_KEY = "no_key", "Not run: no API key"
        SKIPPED = "skipped", "Skipped (another sync was running)"

    started_at = models.DateTimeField(auto_now_add=True, db_index=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.RUNNING)
    trigger = models.CharField(max_length=12, default="scheduler", help_text="scheduler, manual or loop")
    attempt = models.PositiveSmallIntegerField(default=1)
    bbox = models.JSONField(default=list, blank=True, help_text="[south, west, north, east]")
    families = models.JSONField(default=list, blank=True, help_text="Satellite families requested")
    skipped_families = models.JSONField(default=dict, blank=True,
                                        help_text="Family -> reason (e.g. satellite no longer delivered by NASA)")
    window_start = models.DateField(null=True, blank=True)
    window_end = models.DateField(null=True, blank=True)
    requests_made = models.PositiveIntegerField(default=0)
    rows_received = models.PositiveIntegerField(default=0)
    rows_created = models.PositiveIntegerField(default=0, help_text="New detections stored by this run")
    newest_observation = models.DateTimeField(null=True, blank=True,
                                              help_text="Newest acquisition time (UTC) stored for the area after the run")
    availability = models.JSONField(default=dict, blank=True, help_text="FIRMS data_availability snapshot: product -> [min, max]")
    message = models.TextField(blank=True)

    class Meta:
        ordering = ["-started_at"]

    def __str__(self):
        return f"sync {self.started_at:%Y-%m-%d %H:%M} [{self.status}] +{self.rows_created}"

