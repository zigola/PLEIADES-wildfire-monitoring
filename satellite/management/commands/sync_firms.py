"""Pull today's NASA FIRMS data (and the last few days) into the database.

  python manage.py sync_firms                       # Morocco, today + 2 previous days
  python manage.py sync_firms --days 5 --force
  python manage.py sync_firms --globe               # the 9 home-page globe regions
  python manage.py sync_firms --loop 900            # keep refreshing every 15 minutes

Run it from cron / a scheduler for automatic updates. IoT data is not involved.
"""

import time

from django.core.management.base import BaseCommand, CommandError

from satellite import globe
from satellite.analysis.params import DEFAULT_BBOX
from satellite.services import firms_client as fc, live


class Command(BaseCommand):
    help = "Synchronise today's (and recent) NASA FIRMS detections."

    def add_arguments(self, parser):
        parser.add_argument("--bbox", nargs=4, type=float, metavar=("SOUTH", "WEST", "NORTH", "EAST"))
        parser.add_argument("--days", type=int, default=live.DEFAULT_DAYS, help="1-10, counted back from today (UTC)")
        parser.add_argument("--globe", action="store_true", help="Sync the globe regions instead of one area")
        parser.add_argument("--force", action="store_true", help="Ignore the per-area cooldown")
        parser.add_argument("--loop", type=int, default=0, metavar="SECONDS", help="Repeat forever with this pause")

    def handle(self, *args, **o):
        client = fc.FirmsClient()
        if not client.configured:
            raise CommandError("FIRMS_MAP_KEY is not set (https://firms.modaps.eosdis.nasa.gov/api/map_key).")
        while True:
            self.once(client, o)
            if not o["loop"]:
                return
            time.sleep(max(60, o["loop"]))

    def once(self, client, o):
        try:
            availability = client.availability("all")
        except fc.FirmsError as exc:
            raise CommandError(str(exc))
        targets = ([(r["name"], globe.region_bbox(r)) for r in globe.GLOBE_REGIONS] if o["globe"]
                   else [("area", tuple(o["bbox"]) if o["bbox"] else DEFAULT_BBOX)])
        fams = ("MODIS", "VIIRS_SNPP", "VIIRS_NOAA20") if o["globe"] else live.LIVE_FAMILIES
        for name, bbox in targets:
            r = live.sync_recent(client, bbox, o["days"], fams, force=o["force"], availability=availability,
                                 harmonize=not o["globe"])
            style = self.style.SUCCESS if r["status"] == "ok" else self.style.WARNING
            self.stdout.write(style(f"{name}: {r['status']} {r['start']}..{r['end']} received {r['rows_received']}, "
                                    f"new {r['rows_created']}" + (f" ({r.get('message')})" if r["status"] != "ok" else "")))
