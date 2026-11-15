from datetime import date

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Max, Min

from satellite.harmonization import pipeline
from satellite.models import Hotspot


class Command(BaseCommand):
    help = "Flag exact duplicates (NRT vs SP copies) and MODIS/VIIRS overlap groups. Idempotent; never deletes rows."

    def add_arguments(self, parser):
        parser.add_argument("--scope", choices=["real", "demo"], default="real")
        parser.add_argument("--start", type=date.fromisoformat)
        parser.add_argument("--end", type=date.fromisoformat)
        parser.add_argument("--max-minutes", type=int, default=pipeline.DEFAULT_PARAMS["max_minutes"],
                            help="Heuristic time window for MODIS/VIIRS linking (default %(default)s). Not a validated threshold.")
        parser.add_argument("--max-radius-km", type=float, default=pipeline.DEFAULT_PARAMS["max_radius_km"],
                            help="Safety cap on the match radius (default %(default)s). The real radius is the mean of the two pixel sizes.")

    def handle(self, *args, **o):
        span = Hotspot.objects.filter(is_demo=(o["scope"] == "demo")).aggregate(a=Min("acq_date"), b=Max("acq_date"))
        if span["a"] is None:
            raise CommandError(f"No {o['scope']} rows to harmonize.")
        run = pipeline.run(o["start"] or span["a"], o["end"] or span["b"], o["scope"], {"max_minutes": o["max_minutes"], "max_radius_km": o["max_radius_km"]})
        s = run.stats
        self.stdout.write(self.style.SUCCESS(
            f"{run.status}: {s['rows']} rows, {s['exact_duplicates']} exact duplicates flagged, "
            f"{s['overlap_groups']} MODIS/VIIRS overlap groups ({s['overlapping_rows']} rows)."))
