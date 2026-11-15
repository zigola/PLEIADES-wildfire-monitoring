"""Background execution for long imports / harmonization started from the web UI."""

from __future__ import annotations

import logging
import threading

from django.db import connection

from satellite.harmonization import pipeline
from . import importer
from .firms_client import FirmsClient, FirmsError
from .products import PRODUCTS

log = logging.getLogger(__name__)
FAMILIES = {"MODIS": "MODIS", "VIIRS_SNPP": "VIIRS_SNPP", "VIIRS_NOAA20": "VIIRS_NOAA20", "VIIRS_NOAA21": "VIIRS_NOAA21"}


def _in_thread(fn, *args, **kwargs):
    def runner():
        try:
            fn(*args, **kwargs)
        except Exception:  # job/run rows already record the failure
            log.exception("background satellite task failed")
        finally:
            connection.close()
    t = threading.Thread(target=runner, daemon=True)
    t.start()
    return t


def import_family(client, family, bbox, start, end, user=None, background=False):
    """Plan SP/NRT ranges for one satellite family and import them. Returns the created FetchJobs."""
    try:
        availability = client.availability("all") if client.configured else {}
    except FirmsError as exc:
        availability = {}
        log.warning("data_availability unavailable: %s", exc)
    if not client.configured:
        return [importer.run_api_import(client, f"{family}_SP", bbox, start, end, user)]  # records a NO_KEY job
    plan = importer.plan_ranges(family, start, end, availability) if availability else [
        (f"{family}_SP", start, end)]
    jobs = [importer.create_api_job(p, bbox, s, e, user, availability) for p, s, e in plan]
    if not jobs:
        raise ValueError("FIRMS has no data for this product family in the requested period (see data_availability).")
    for job in jobs:
        args = (client, job.product, bbox, job.start_date, job.end_date, user, availability, job)
        if background:
            _in_thread(importer.run_api_import, *args)
        else:
            importer.run_api_import(*args)
    return jobs


def harmonize(start, end, scope, background=False):
    if background:
        return _in_thread(pipeline.run, start, end, scope)
    return pipeline.run(start, end, scope)
