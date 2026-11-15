"""Import NASA FIRMS active-fire data: from the API (needs FIRMS_MAP_KEY) or from downloaded CSV files.

Examples
  python manage.py import_firms --api --family MODIS --bbox 20.84 -17.06 35.74 -1.54 --start 2024-06-01 --end 2024-08-31
  python manage.py import_firms --api --family VIIRS_SNPP --family VIIRS_NOAA20 --bbox 20.84 -17.06 35.74 -1.54 --start 2024-06-01 --end 2024-08-31

--bbox is SOUTH WEST NORTH EAST in decimal degrees (POLARIS order). It is sent to FIRMS as west,south,east,north.
Dates are UTC calendar days, YYYY-MM-DD, both inclusive. The end date cannot be in the future.
  python manage.py import_firms MODIS_archive.csv VIIRS_SNPP_archive.csv      (coverage inferred)
  python manage.py import_firms --bbox S W N E --start D --end D file.csv    (coverage declared)
"""

from datetime import date

from django.core.management.base import BaseCommand, CommandError

from satellite.models import FetchJob, Hotspot
from satellite.services import firms_client as fc, importer, tasks


def _date(value):
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise CommandError(f"'{value}' is not a date like 2024-07-31")


class Command(BaseCommand):
    help = "Import NASA FIRMS MODIS/VIIRS active-fire data (API or CSV)."

    def add_arguments(self, parser):
        parser.add_argument("paths", nargs="*", help="FIRMS CSV files")
        parser.add_argument("--api", action="store_true", help="Use the FIRMS Area API (FIRMS_MAP_KEY required)")
        parser.add_argument("--family", action="append", choices=list(tasks.FAMILIES),
                            help="Satellite family for --api (repeatable). SP is used where available, NRT after it")
        parser.add_argument("--product", action="append", help="Explicit FIRMS source id, e.g. MODIS_SP (bypasses planning)")
        parser.add_argument("--bbox", nargs=4, type=float, metavar=("SOUTH", "WEST", "NORTH", "EAST"),
                            help="Area of interest in decimal degrees, e.g. --bbox 20.84 -17.06 35.74 -1.54")
        parser.add_argument("--start", type=_date)
        parser.add_argument("--end", type=_date)
        parser.add_argument("--sensor", choices=["MODIS", "VIIRS"], help="Override CSV sensor detection")
        parser.add_argument("--purge-demo", action="store_true", help="Delete synthetic demo rows first")
        parser.add_argument("--check-key", action="store_true", help="Only test the MAP_KEY and show the transaction budget")

    def handle(self, *args, **o):
        if o["purge_demo"]:
            deleted, _ = Hotspot.objects.filter(is_demo=True).delete()
            FetchJob.objects.filter(kind=FetchJob.Kind.DEMO).delete()  # demo coverage must not outlive demo rows
            self.stdout.write(f"Removed {deleted} synthetic demo rows (real NASA rows are never touched).")
        client = fc.FirmsClient()
        if o["check_key"]:
            return self.check_key(client)
        if o["api"]:
            return self.from_api(client, o)
        if not o["paths"]:
            raise CommandError("Give CSV files or use --api.")
        bbox = tuple(o["bbox"]) if o["bbox"] else None
        for path in o["paths"]:
            try:
                job = importer.import_csv(path, o["sensor"], bbox, o["start"], o["end"])
            except (OSError, ValueError) as exc:
                raise CommandError(f"{path}: {exc}")
            self.report(job)

    def check_key(self, client):
        try:
            st = client.key_status()
        except fc.FirmsError as exc:
            raise CommandError(str(exc))
        self.stdout.write(self.style.SUCCESS(
            f"MAP_KEY accepted. {st.get('current_transactions')}/{st.get('transaction_limit')} transactions "
            f"in the current {st.get('transaction_interval')}."))

    def from_api(self, client, o):
        if not client.configured:
            raise CommandError("FIRMS_MAP_KEY is not set. Get a free key at https://firms.modaps.eosdis.nasa.gov/api/map_key "
                               "and put FIRMS_MAP_KEY=... in the environment or in .env.")
        if not (o["bbox"] and o["start"] and o["end"]):
            raise CommandError("--api needs --bbox S W N E, --start and --end.")
        if o["product"]:
            from satellite.services.products import PRODUCTS
            bad = [p for p in o["product"] if p not in PRODUCTS]
            if bad:
                raise CommandError(f"Unknown FIRMS product(s) {bad}. Valid: {', '.join(PRODUCTS)}")
        if o["start"] > o["end"]:
            raise CommandError("--start must not be after --end.")
        bbox = tuple(o["bbox"])
        south, west, north, east = bbox
        if not (-90 <= south < north <= 90):
            raise CommandError("--bbox is SOUTH WEST NORTH EAST: need -90 <= SOUTH < NORTH <= 90.")
        if not (-180 <= west < east <= 180):
            raise CommandError("--bbox is SOUTH WEST NORTH EAST: need -180 <= WEST < EAST <= 180 "
                               "(areas crossing the antimeridian are not supported).")
        if o["end"] > date.today():
            raise CommandError("--end cannot be in the future.")
        try:
            if o["product"]:
                for pid in o["product"]:
                    self.report(importer.run_api_import(client, pid, bbox, o["start"], o["end"]))
            else:
                for fam in o["family"] or ["MODIS", "VIIRS_SNPP", "VIIRS_NOAA20"]:
                    for job in tasks.import_family(client, fam, bbox, o["start"], o["end"]):
                        self.report(job)
        except (fc.FirmsError, ValueError) as exc:
            raise CommandError(str(exc))

    def report(self, job):
        style = self.style.SUCCESS if job.status == "success" else self.style.WARNING
        self.stdout.write(style(
            f"{job.product} {job.start_date}..{job.end_date} [{job.status}]: received {job.rows_received}, "
            f"created {job.rows_created}, duplicates {job.rows_duplicate}, rejected {job.rows_invalid}"
            + (f"  ({job.message.strip()[:200]})" if job.message else "")))
