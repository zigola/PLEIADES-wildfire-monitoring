"""Import NASA FIRMS history year by year, with progress and resume.

  python manage.py import_history --from-year 2015 --to-year 2024 --preset tadla
  python manage.py import_history --year 2019 --year 2020 --bbox 20.84 -17.06 35.74 -1.54 --family MODIS
  python manage.py import_history --from-year 2012 --to-year 2026 --dry-run      # show the plan only

* Each satellite family is requested only for the days FIRMS says it has data (``data_availability``): MODIS from
  November 2000, VIIRS Suomi-NPP, NOAA-20 and NOAA-21 from their own start dates. Nothing before a satellite's start
  date is requested and nothing is generated.
* A (family, year) chunk already fully covered by a successful import of the same or a larger area is skipped, so the
  command can be interrupted and run again (``--force`` re-imports).
* Rows are stored unchanged with their product, version and import job; exact duplicates are discarded.
* The FIRMS Area API takes about 5 days per request, so a full MODIS history of an area needs roughly 1,900 requests.
  The client waits when NASA's transaction limit (5,000 per 10 minutes per key) is reached.
"""

from datetime import date, timedelta

from django.core.management.base import BaseCommand, CommandError

from satellite.analysis.params import DEFAULT_BBOX
from satellite.models import FetchJob
from satellite.services import firms_client as fc, tasks

PRESETS = {
    "morocco": DEFAULT_BBOX,
    "tadla": (32.0, -7.2, 33.0, -5.6),
    "north": (33.8, -6.8, 35.9, -3.4),
    "atlas": (31.0, -8.0, 33.8, -3.5),
    "oriental": (33.0, -3.5, 35.3, -1.2),
    "souss": (29.5, -10.2, 31.0, -8.0),
}
OK = ("success",)


def family_span(family, availability):
    spans = [availability.get(f"{family}_SP"), availability.get(f"{family}_NRT")]
    spans = [s for s in spans if s]
    if not spans:
        return None
    return min(s[0] for s in spans), max(s[1] for s in spans)


def chunk_for(year, span, today):
    """The part of ``year`` that the family has data for (None if none)."""
    start, end = max(date(year, 1, 1), span[0]), min(date(year, 12, 31), span[1], today)
    return (start, end) if start <= end else None


def already_imported(family, bbox, start, end):
    s, w, n, e = bbox
    for job in FetchJob.objects.filter(status__in=OK, kind=FetchJob.Kind.API, product__startswith=f"{family}_",
                                       south__lte=s, west__lte=w, north__gte=n, east__gte=e):
        for a, b in job.covered_ranges or []:
            if date.fromisoformat(a) <= start and date.fromisoformat(b) >= end:
                return True
    return False


class Command(BaseCommand):
    help = "Import NASA FIRMS history by year (all satellites that have data in each year), resumable."

    def add_arguments(self, parser):
        parser.add_argument("--year", action="append", type=int, help="Year to import (repeatable)")
        parser.add_argument("--from-year", type=int)
        parser.add_argument("--to-year", type=int)
        parser.add_argument("--preset", choices=sorted(PRESETS), help="Named area (default: morocco)")
        parser.add_argument("--bbox", nargs=4, type=float, metavar=("SOUTH", "WEST", "NORTH", "EAST"))
        parser.add_argument("--family", action="append", choices=list(tasks.FAMILIES), help="Default: all four families")
        parser.add_argument("--dry-run", action="store_true", help="Print the plan, request nothing")
        parser.add_argument("--force", action="store_true", help="Re-import chunks that are already covered")

    def handle(self, *args, **o):
        today = date.today()
        years = sorted(set(o["year"] or []) | set(range(o["from_year"], o["to_year"] + 1) if o["from_year"] and o["to_year"] else []))
        if not years:
            raise CommandError("Give --year, or --from-year and --to-year.")
        if years[0] < 2000 or years[-1] > today.year:
            raise CommandError(f"Years must be between 2000 and {today.year} (NASA active-fire data start in November 2000).")
        bbox = tuple(o["bbox"]) if o["bbox"] else PRESETS[o["preset"] or "morocco"]
        south, west, north, east = bbox
        if not (-90 <= south < north <= 90 and -180 <= west < east <= 180):
            raise CommandError("--bbox is SOUTH WEST NORTH EAST (degrees), south < north and west < east.")
        families = o["family"] or list(tasks.FAMILIES)
        client = fc.FirmsClient()
        if not client.configured:
            raise CommandError("FIRMS_MAP_KEY is not set (https://firms.modaps.eosdis.nasa.gov/api/map_key).")
        try:
            availability = client.availability("all")
        except fc.FirmsError as exc:
            raise CommandError(f"Could not read FIRMS data availability: {exc}")
        plan = []
        for fam in families:
            span = family_span(fam, availability)
            if not span:
                self.stdout.write(self.style.WARNING(f"{fam}: not listed by FIRMS, skipped."))
                continue
            self.stdout.write(f"{fam}: FIRMS has data from {span[0]} to {span[1]}.")
            for y in years:
                ch = chunk_for(y, span, today)
                if ch:
                    plan.append((fam, y, *ch))
        if not plan:
            raise CommandError("Nothing to import: no selected satellite has data in the selected years.")
        total_days = sum((e - s).days + 1 for _f, _y, s, e in plan)
        self.stdout.write(f"Plan: {len(plan)} chunks, {total_days} satellite-days, about {-(-total_days // 5)} API requests.")
        if o["dry_run"]:
            for fam, y, s, e in plan:
                self.stdout.write(f"  {fam} {y}: {s}..{e}" + ("  (already imported)" if not o["force"] and already_imported(fam, bbox, s, e) else ""))
            return
        done = failed = skipped = 0
        for i, (fam, y, s, e) in enumerate(plan, 1):
            tag = f"[{i}/{len(plan)}] {fam} {y} ({s}..{e})"
            if not o["force"] and already_imported(fam, bbox, s, e):
                self.stdout.write(f"{tag}: already imported, skipped"); skipped += 1; continue
            try:
                jobs = tasks.import_family(client, fam, bbox, s, e)
            except (fc.FirmsError, ValueError) as exc:
                self.stdout.write(self.style.ERROR(f"{tag}: {exc}")); failed += 1; continue
            ok = all(j.status in OK for j in jobs)
            line = f"{tag}: " + ", ".join(f"{j.product} {j.status} (+{j.rows_created} new, {j.rows_duplicate} duplicates)" for j in jobs)
            self.stdout.write((self.style.SUCCESS if ok else self.style.WARNING)(line))
            done += ok
            failed += (not ok)
        self.stdout.write(f"Finished: {done} imported, {skipped} skipped, {failed} with problems. Run again to retry the problems.")
        if failed:
            raise CommandError("Some chunks did not complete; they were logged in Data management > import jobs.")
