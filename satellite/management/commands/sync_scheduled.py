"""Scheduled NASA FIRMS check (run it every 10 minutes). Records every check in ``SyncRun``.

  python manage.py sync_scheduled                       # one check of Morocco, then exit (use with cron / Task Scheduler)
  python manage.py sync_scheduled --bbox 32 -7.2 33 -5.6
  python manage.py sync_scheduled --loop                # stay running, one check every FIRMS_SYNC_INTERVAL_SECONDS (600)
  python manage.py sync_scheduled --attempts 3 --backoff 30

Cron (Linux/macOS):    */10 * * * *  cd /path/to/project && .venv/bin/python manage.py sync_scheduled
Windows Task Scheduler: repeat every 10 minutes, program .venv\\Scripts\\python.exe, arguments manage.py sync_scheduled

Only NASA rows are written (duplicates are discarded). IoT data and the AI assistant are not touched.
"""

import time

from django.core.management.base import BaseCommand, CommandError

from satellite.analysis.params import DEFAULT_BBOX
from satellite.models import SyncRun
from satellite.services import firms_client as fc, sync


class Command(BaseCommand):
    help = "Check NASA FIRMS for new detections (incremental, logged in SyncRun)."

    def add_arguments(self, parser):
        parser.add_argument("--bbox", nargs=4, type=float, metavar=("SOUTH", "WEST", "NORTH", "EAST"),
                            help="Area to check (default: Morocco and Western Sahara)")
        parser.add_argument("--days", type=int, default=None, help="Force the window (1-10 days); default is incremental")
        parser.add_argument("--attempts", type=int, default=3, help="Tries per check when NASA fails or rate-limits")
        parser.add_argument("--backoff", type=int, default=30, help="Seconds before the first retry (doubles each time)")
        parser.add_argument("--loop", action="store_true", help="Repeat forever with FIRMS_SYNC_INTERVAL_SECONDS between checks")

    def handle(self, *args, **o):
        client = fc.FirmsClient()
        if not client.configured:
            raise CommandError("FIRMS_MAP_KEY is not set (https://firms.modaps.eosdis.nasa.gov/api/map_key).")
        bbox = tuple(o["bbox"]) if o["bbox"] else DEFAULT_BBOX
        while True:
            started = time.monotonic()
            runs = sync.run_with_retry(client, bbox, attempts=o["attempts"], backoff=o["backoff"], days=o["days"],
                                       trigger="loop" if o["loop"] else "scheduler")
            run = runs[-1]
            ok = run.status in (SyncRun.Status.OK, SyncRun.Status.PARTIAL, SyncRun.Status.SKIPPED)
            line = (f"{run.started_at:%Y-%m-%d %H:%M:%S} {run.status}: window {run.window_start}..{run.window_end}, "
                    f"received {run.rows_received}, new {run.rows_created}, attempts {len(runs)}")
            if run.skipped_families:
                line += " | skipped: " + ", ".join(run.skipped_families)
            self.stdout.write((self.style.SUCCESS if ok else self.style.ERROR)(line))
            if run.message and not ok:
                self.stderr.write(run.message[:300])
            if not o["loop"]:
                if not ok:
                    raise CommandError(f"Synchronisation ended with status {run.status}.")
                return
            time.sleep(max(60, sync.interval_seconds() - (time.monotonic() - started)))
