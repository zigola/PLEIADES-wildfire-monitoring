"""Django tests: A/B/C/D answer endpoint, scheduled synchronisation, same-date and history endpoints.

No network: every FIRMS session is mocked (see test_live). The pure statistics are tested in test_estimate.
These tests were written without access to a Django environment: run them with
    python manage.py test satellite.test_fallback
"""

from datetime import date, timedelta
from unittest import mock

from django.test import TestCase
from django.urls import reverse

from iot.models import Reading, Station
from satellite.models import FetchJob, Hotspot, SyncRun
from satellite.services import firms_client as fc, live, sync
from satellite.test_live import AOI, TODAY, Resp, avail, client, fake_session

Q = {"south": 30, "west": -10, "north": 36, "east": -1}


def seed_history(years, counts, sensor="MODIS", frp=10.0):
    """Real stored history: one successful import per year covering the whole year, with ``counts[i]`` detections on
    the same calendar day as TODAY in year ``years[i]``."""
    for y, n in zip(years, counts):
        FetchJob.objects.create(kind=FetchJob.Kind.API, product=f"{sensor}_SP", sensor=sensor, south=AOI[0], west=AOI[1],
                                north=AOI[2], east=AOI[3], start_date=date(y, 1, 1), end_date=date(y, 12, 31),
                                status=FetchJob.Status.SUCCESS,
                                covered_ranges=[[date(y, 1, 1).isoformat(), date(y, 12, 31).isoformat()]])
        for i in range(n):
            Hotspot.objects.create(sensor=sensor, satellite="Terra" if sensor == "MODIS" else "N", latitude=34.5,
                                   longitude=-5.0 + i * 0.01, acq_date=date(y, TODAY.month, TODAY.day), acq_time=1000 + i,
                                   product=f"{sensor}_SP", confidence="n", frp=frp)


def stale_snpp_session():
    sess = fake_session(TODAY)
    original = sess.get.side_effect

    def get(url, timeout=None):
        if "data_availability" in url:
            text = (avail(TODAY).replace("VIIRS_SNPP_NRT,2026-06-01,2026-11-14", "VIIRS_SNPP_NRT,2026-06-01,2026-11-02")
                    .replace("VIIRS_SNPP_SP,2012-01-19,2026-06-30", "VIIRS_SNPP_SP,2012-01-19,2026-11-02"))
            return Resp(text)
        return original(url, timeout)
    sess.get.side_effect = get
    return sess


class TodayEndpointTests(TestCase):
    def get(self, **extra):
        with mock.patch("satellite.services.live.utc_today", return_value=TODAY):
            return self.client.get(reverse("satellite:today"), {**Q, "date": TODAY.isoformat(), **extra})

    def by(self, payload):
        return {s["sensor"]: s for s in payload["sensors"]}

    def test_empty_database_is_unavailable(self):
        r = self.get()
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertEqual(d["primary_category"], "D")
        self.assertTrue(all(s["category"] == "D" and not s.get("estimate") for s in d["sensors"]))
        self.assertFalse(Hotspot.objects.exists())

    def test_history_gives_a_labelled_estimate_and_stores_nothing(self):
        seed_history(range(2018, 2024), [1, 2, 1, 3, 2, 4])
        before = Hotspot.objects.count()
        d = self.get().json()
        modis = self.by(d)["MODIS"]
        self.assertEqual(modis["category"], "C")
        self.assertNotIn("detections", modis)
        self.assertEqual(modis["estimate"]["n_years"], 6)
        self.assertEqual(modis["estimate"]["reference_period"], [2018, 2023])
        self.assertEqual(modis["estimate"]["n_detections"], 13)
        self.assertEqual([h["detections"] for h in modis["historical"]], [1, 2, 1, 3, 2, 4])
        self.assertEqual(self.by(d)["VIIRS"]["category"], "D")           # no VIIRS history: nothing is invented
        self.assertEqual(d["primary_category"], "C")
        self.assertEqual(Hotspot.objects.count(), before)               # an estimate never becomes a Hotspot

    def test_too_little_history_is_unavailable_with_reason(self):
        seed_history(range(2020, 2024), [1, 2, 1, 3])
        modis = self.by(self.get().json())["MODIS"]
        self.assertEqual(modis["category"], "D")
        self.assertIn("cannot be generated", modis["reason"])

    def test_estimate_is_replaced_by_verified_observations_after_the_next_sync(self):
        seed_history(range(2018, 2024), [1, 2, 1, 3, 2, 4])
        self.assertEqual(self.by(self.get().json())["MODIS"]["category"], "C")
        seeded = Hotspot.objects.filter(product="MODIS_SP").count()
        run = sync.run_once(client(), AOI, today=TODAY)
        self.assertEqual(run.status, SyncRun.Status.OK, run.message)
        self.assertGreater(run.rows_created, 0)
        modis = self.by(self.get().json())["MODIS"]
        self.assertEqual(modis["category"], "A")
        self.assertGreaterEqual(modis["detections"], 1)
        self.assertNotIn("estimate", modis)
        self.assertEqual(Hotspot.objects.filter(product="MODIS_SP").count(), seeded)   # history untouched
        self.assertTrue(Hotspot.objects.filter(acq_date=TODAY, is_demo=False, product="MODIS_NRT").exists())

    def test_verified_zero_is_not_replaced_by_an_estimate(self):
        seed_history(range(2018, 2024), [1, 2, 1, 3, 2, 4])
        FetchJob.objects.create(kind=FetchJob.Kind.API, product="MODIS_NRT", sensor="MODIS", south=AOI[0], west=AOI[1],
                                north=AOI[2], east=AOI[3], start_date=TODAY, end_date=TODAY, status=FetchJob.Status.SUCCESS,
                                covered_ranges=[[TODAY.isoformat(), TODAY.isoformat()]])
        modis = self.by(self.get().json())["MODIS"]
        self.assertEqual((modis["category"], modis["detections"], modis.get("verified_zero")), ("A", 0, True))

    def test_future_date_and_bad_input(self):
        with mock.patch("satellite.services.live.utc_today", return_value=TODAY):
            future = self.client.get(reverse("satellite:today"), {**Q, "date": (TODAY + timedelta(days=2)).isoformat()}).json()
            self.assertTrue(future["is_future"])
            self.assertEqual(future["primary_category"], "D")
            self.assertEqual(self.client.get(reverse("satellite:today"), {**Q, "date": "1999-05-01"}).status_code, 400)
            self.assertEqual(self.client.get(reverse("satellite:today"), {"south": 50, "north": 40}).status_code, 400)
            self.assertEqual(self.client.post(reverse("satellite:today")).status_code, 405)

    def test_demo_rows_are_never_used(self):
        Hotspot.objects.create(sensor="MODIS", satellite="Terra", latitude=34.5, longitude=-5.0, acq_date=TODAY, acq_time=900,
                               product="", is_demo=True, confidence="n")
        d = self.get().json()
        self.assertNotIn("detections", self.by(d)["MODIS"])
        self.assertEqual(d["primary_category"], "D")


class ScheduledSyncTests(TestCase):
    def test_run_records_window_and_new_rows(self):
        run = sync.run_once(client(), AOI, today=TODAY)
        self.assertEqual(run.status, SyncRun.Status.OK, run.message)
        self.assertEqual((run.window_start, run.window_end), (TODAY - timedelta(days=2), TODAY))
        self.assertGreater(run.rows_received, 0)
        self.assertGreater(run.rows_created, 0)
        self.assertIsNotNone(run.finished_at)
        self.assertIsNotNone(run.newest_observation)
        self.assertEqual(run.newest_observation.date(), TODAY)
        self.assertEqual(run.bbox, list(live.norm_bbox(AOI)))

    def test_repeating_adds_no_duplicates(self):
        first = sync.run_once(client(), AOI, today=TODAY)
        count = Hotspot.objects.count()
        second = sync.run_once(client(), AOI, today=TODAY)
        self.assertEqual(second.rows_created, 0)
        self.assertEqual(Hotspot.objects.count(), count)
        self.assertEqual(sync.status_payload(AOI)["last_new_data_run"], first.id)   # nothing new: marker unchanged

    def test_satellite_that_stopped_is_skipped_and_reported(self):
        run = sync.run_once(client(stale_snpp_session()), AOI, today=TODAY)
        self.assertIn("VIIRS_SNPP", run.skipped_families)
        self.assertIn("2026-11-02", run.skipped_families["VIIRS_SNPP"])
        self.assertNotIn("VIIRS_SNPP", run.families)
        self.assertFalse(FetchJob.objects.filter(product__startswith="VIIRS_SNPP").exists())
        self.assertIn(run.status, (SyncRun.Status.OK, SyncRun.Status.PARTIAL))

    def test_overlapping_run_is_skipped_without_calling_nasa(self):
        SyncRun.objects.create(status=SyncRun.Status.RUNNING)
        sess = fake_session(TODAY)
        run = sync.run_once(client(sess), AOI, today=TODAY)
        self.assertEqual(run.status, SyncRun.Status.SKIPPED)
        sess.get.assert_not_called()

    def test_no_key_is_recorded_not_raised(self):
        run = sync.run_once(fc.FirmsClient(map_key=""), AOI, today=TODAY)
        self.assertEqual(run.status, SyncRun.Status.NO_KEY)
        self.assertEqual(Hotspot.objects.count(), 0)

    def test_retry_backs_off_and_stops_on_success(self):
        waits = []
        seq = [mock.Mock(status=SyncRun.Status.FAILED), mock.Mock(status=SyncRun.Status.RATE_LIMITED), mock.Mock(status=SyncRun.Status.OK)]
        with mock.patch.object(sync, "run_once", side_effect=seq):
            runs = sync.run_with_retry(client(), AOI, attempts=3, backoff=30, sleep=waits.append)
        self.assertEqual(len(runs), 3)
        self.assertEqual(waits, [30, 60])                  # 30 s, then max(60 s doubled, 60 s rate-limit floor)

    def test_retry_gives_up_after_the_last_attempt(self):
        waits = []
        with mock.patch.object(sync, "run_once", return_value=mock.Mock(status=SyncRun.Status.FAILED)) as m:
            runs = sync.run_with_retry(client(), AOI, attempts=2, backoff=5, sleep=waits.append)
        self.assertEqual((len(runs), m.call_count, waits), (2, 2, [5]))

    def test_status_endpoint_reports_last_success_and_notes_publication_limit(self):
        self.assertIsNone(self.client.get(reverse("satellite:sync_status")).json()["last_run"])
        sync.run_once(client(), AOI, today=TODAY)
        d = self.client.get(reverse("satellite:sync_status"), Q).json()
        self.assertEqual(d["last_run"]["status"], "ok")
        self.assertIsNotNone(d["last_success_at"])
        self.assertIsNotNone(d["next_check_utc"])
        self.assertEqual(d["interval_seconds"], 600)
        self.assertIn("publication time", d["note"])

    def test_sync_leaves_iot_tables_alone(self):
        stations, readings = Station.objects.count(), Reading.objects.count()
        sync.run_once(client(), AOI, today=TODAY)
        self.assertEqual((Station.objects.count(), Reading.objects.count()), (stations, readings))


class HistoryEndpointTests(TestCase):
    def setUp(self):
        seed_history(range(2018, 2024), [1, 2, 1, 3, 2, 4])

    def test_years_lists_only_stored_years_with_satellites(self):
        d = self.client.get(reverse("satellite:history_years"), Q).json()
        self.assertEqual([y["year"] for y in d["years"]], list(range(2018, 2024)))
        self.assertEqual(d["years"][-1]["MODIS"], 4)
        self.assertEqual(d["years"][-1]["satellites"], {"Terra": 4})
        self.assertIn("MODIS", d["coverage"])

    def test_same_date_distinguishes_zero_from_not_imported(self):
        d = self.client.get(reverse("satellite:same_date"), {**Q, "date": TODAY.isoformat(), "sensor": "MODIS"}).json()
        rows = {y["year"]: y["sensors"]["MODIS"] for y in d["years"]}
        self.assertEqual(rows[2021]["detections"], 3)
        self.assertEqual(rows[2021]["frp"]["mean_mw"], 10.0)
        self.assertEqual(rows[2025]["status"], "no_data")          # never imported: unknown, not zero
        self.assertIsNone(rows[2025]["detections"])
        self.assertEqual(d["years"][-1]["year"], TODAY.year)

    def test_same_date_rejects_bad_dates(self):
        self.assertEqual(self.client.get(reverse("satellite:same_date"), {**Q, "date": "1998-01-01"}).status_code, 400)
        self.assertEqual(self.client.get(reverse("satellite:same_date"), {**Q, "date": "nope"}).status_code, 400)

    def test_imports_listing_exposes_progress(self):
        job = FetchJob.objects.get(start_date=date(2019, 1, 1))
        job.days_ok = 100
        job.save()
        rows = {r["id"]: r for r in self.client.get(reverse("satellite:imports")).json()["imports"]}
        self.assertEqual(rows[job.id]["days_total"], 365)
        self.assertEqual(rows[job.id]["progress_pct"], 27)


class PageTests(TestCase):
    def test_today_page_and_navigation(self):
        r = self.client.get(reverse("sat:today"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "today-app")
        self.assertContains(r, "Today &amp; estimates")
        self.assertContains(self.client.get(reverse("sat:map")), "sat_popup.js")
        self.assertContains(self.client.get(reverse("public_dashboard")), "same-date-panel")

    def test_detection_payload_carries_provenance_fields(self):
        seed_history([2023], [1])
        job = FetchJob.objects.first()
        Hotspot.objects.update(fetch=job)
        job.finished_at = job.started_at
        job.save()
        g = self.client.get(reverse("satellite:detections"), {**Q, "format": "geojson", "min_conf": "all", "start": "2023-01-01", "end": "2023-12-31"}).json()
        props = g["features"][0]["properties"]
        self.assertEqual(props["product"], "MODIS_SP")
        self.assertIn("imported_at", props)
