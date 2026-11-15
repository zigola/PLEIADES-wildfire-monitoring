from django.core.management.base import BaseCommand
from django.utils import timezone

from satellite import demo
from satellite.models import FetchJob, Hotspot


class Command(BaseCommand):
    help = "Create SYNTHETIC MODIS/VIIRS-like hotspots for demos (flagged is_demo). Not real fire data."

    def add_arguments(self, parser):
        parser.add_argument("--seed", type=int, default=7)
        parser.add_argument("--scale", type=float, default=1.0, help="Multiplies the number of fires")

    def handle(self, *args, **opts):
        deleted, _ = Hotspot.objects.filter(is_demo=True).delete()
        FetchJob.objects.filter(kind=FetchJob.Kind.DEMO).delete()
        jobs = {s: FetchJob.objects.create(
            kind=FetchJob.Kind.DEMO, product="SYNTHETIC", sensor=s, south=demo.AOI[0], west=demo.AOI[1],
            north=demo.AOI[2], east=demo.AOI[3], start_date=demo.MODIS_START if s == "MODIS" else demo.VIIRS_START,
            end_date=demo.DEFAULT_END, source_label="Synthetic generator (satellite/demo.py), NOT NASA data",
            status=FetchJob.Status.SUCCESS, finished_at=timezone.now(),
            covered_ranges=[[(demo.MODIS_START if s == "MODIS" else demo.VIIRS_START).isoformat(),
                             demo.DEFAULT_END.isoformat()]]) for s in ("MODIS", "VIIRS")}
        batch, total = [], 0
        for row in demo.generate(seed=opts["seed"], scale=opts["scale"]):
            batch.append(Hotspot(fetch=jobs[row["sensor"]], instrument=row["sensor"], **row))
            if len(batch) >= 5000:
                Hotspot.objects.bulk_create(batch)
                total += len(batch)
                batch = []
        if batch:
            Hotspot.objects.bulk_create(batch)
            total += len(batch)
        for s, j in jobs.items():
            j.rows_created = j.rows_received = Hotspot.objects.filter(fetch=j).count()
            j.save(update_fields=["rows_created", "rows_received"])
        self.stdout.write(self.style.SUCCESS(
            f"Removed {deleted} old demo rows, created {total} SYNTHETIC hotspots (not NASA observations)."))
