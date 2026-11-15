"""Tests for FIRMS parsing, client error handling, import, harmonization, calendar, anomalies, views."""

import io
import json
import tempfile
from datetime import date, timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from satellite.analysis import calendar as cal, coverage as cov, spatial
from satellite.analysis.params import BadRequest, common_filters, hotspots
from satellite.harmonization import pipeline
from satellite.models import FetchJob, Hotspot
from satellite.services import firms_client as fc, importer
from satellite.services.parsing import RecordError, classify_confidence, detect_sensor, normalize_row

MODIS_HEADER = "latitude,longitude,brightness,scan,track,acq_date,acq_time,satellite,instrument,confidence,version,bright_t31,frp,daynight"
VIIRS_HEADER = "latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,instrument,confidence,version,bright_ti5,frp,daynight"
M_ROW = "34.5,-5.5,330.1,1.1,1.0,2024-07-10,1030,Terra,MODIS,85,6.1NRT,300.2,12.5,D"
V_ROW = "34.5003,-5.5002,340.0,0.4,0.4,2024-07-10,1031,N,VIIRS,n,2.0NRT,295.0,3.1,D"
AOI = (30.0, -10.0, 36.0, -1.0)


def mrow(**kw):
    base = dict(latitude="34.5", longitude="-5.5", brightness="330.1", scan="1.1", track="1.0", acq_date="2024-07-10",
                acq_time="1030", satellite="Terra", instrument="MODIS", confidence="85", version="6.1NRT",
                bright_t31="300.2", frp="12.5", daynight="D")
    base.update(kw)
    return base


class FakeResp:
    def __init__(self, text, status=200):
        self.text, self.status_code = text, status


class ParsingTests(SimpleTestCase):
    def test_modis_row(self):
        r = normalize_row(mrow(), "MODIS", "MODIS_NRT")
        self.assertEqual((r["sensor"], r["satellite"], r["brightness"], r["brightness_2"], r["acq_time"]), ("MODIS", "Terra", 330.1, 300.2, 1030))
        self.assertEqual((r["confidence"], r["confidence_raw"]), ("h", "85"))

    def test_viirs_row_uses_viirs_channels(self):
        row = dict(latitude="1", longitude="2", bright_ti4="340", bright_ti5="295", acq_date="2024-01-01", acq_time="1", satellite="N20",
                   instrument="VIIRS", confidence="h", frp="2", daynight="N", scan="0.4", track="0.4", version="2.0NRT")
        r = normalize_row(row, "VIIRS", "VIIRS_NOAA20_NRT")
        self.assertEqual((r["brightness"], r["brightness_2"], r["acq_time"], r["confidence"]), (340.0, 295.0, 1, "h"))

    def test_sensor_detection(self):
        self.assertEqual(detect_sensor(MODIS_HEADER.split(",")), "MODIS")
        self.assertEqual(detect_sensor(VIIRS_HEADER.split(",")), "VIIRS")
        self.assertIsNone(detect_sensor(["a", "b"]))

    def test_invalid_records(self):
        cases = {"missing_coordinate": mrow(latitude=""), "coordinate_out_of_range": mrow(latitude="91"),
                 "bad_date": mrow(acq_date="2024-13-40"), "bad_time": mrow(acq_time="2575"), "negative_frp": mrow(frp="-1"),
                 "bad_number": mrow(brightness="nan")}
        for code, row in cases.items():
            with self.assertRaises(RecordError, msg=code) as cm:
                normalize_row(row, "MODIS", "MODIS_SP")
            self.assertEqual(cm.exception.code, code)
        with self.assertRaises(RecordError):
            normalize_row(mrow(longitude="abc"), "MODIS", "MODIS_SP")

    def test_missing_optional_values_stay_null(self):
        r = normalize_row(mrow(frp="", confidence="", brightness=""), "MODIS", "MODIS_SP")
        self.assertIsNone(r["frp"]); self.assertIsNone(r["brightness"]); self.assertEqual((r["confidence"], r["confidence_raw"]), ("", ""))

    def test_confidence_classes(self):
        self.assertEqual(classify_confidence("29", "MODIS")[0], "l")
        self.assertEqual(classify_confidence("30", "MODIS")[0], "n")
        self.assertEqual(classify_confidence("80", "MODIS")[0], "h")
        self.assertEqual(classify_confidence("150", "MODIS")[0], "")
        self.assertEqual(classify_confidence("high", "VIIRS")[0], "h")


class ClientTests(SimpleTestCase):
    def mk(self, responses, key="SECRETKEY"):
        sess = mock.Mock()
        sess.get.side_effect = responses
        return fc.FirmsClient(map_key=key, session=sess, retries=1, backoff=0, sleep=lambda s: None), sess

    def test_missing_key(self):
        with self.assertRaises(fc.MissingKey):
            fc.FirmsClient(map_key="").fetch_area("MODIS_SP", AOI, date(2024, 7, 1), 1)

    def test_url_uses_documented_order_and_limits(self):
        c, sess = self.mk([FakeResp(MODIS_HEADER + "\n" + M_ROW)])
        rows = c.fetch_area("MODIS_NRT", AOI, date(2024, 7, 10), 5)
        self.assertEqual(len(rows), 1)
        url = sess.get.call_args[0][0]
        self.assertTrue(url.endswith("/api/area/csv/SECRETKEY/MODIS_NRT/-10,30,-1,36/5/2024-07-10"), url)
        with self.assertRaises(ValueError):
            c.fetch_area("MODIS_NRT", AOI, date(2024, 7, 10), 6)
        with self.assertRaises(ValueError):
            c.fetch_area("NOT_A_PRODUCT", AOI, date(2024, 7, 10), 1)

    def test_error_bodies_and_statuses(self):
        for body, exc in [("Invalid MAP_KEY.", fc.InvalidKey), ("Exceeded transaction limit", fc.RateLimited), ("<html>oops</html>", fc.BadResponse)]:
            c, _ = self.mk([FakeResp(body)])
            with self.assertRaises(exc):
                c.fetch_area("MODIS_SP", AOI, date(2024, 7, 1), 1)
        c, _ = self.mk([FakeResp("", 403)])
        with self.assertRaises(fc.InvalidKey):
            c.fetch_area("MODIS_SP", AOI, date(2024, 7, 1), 1)
        c, _ = self.mk([FakeResp("", 429)])
        with self.assertRaises(fc.RateLimited):
            c.fetch_area("MODIS_SP", AOI, date(2024, 7, 1), 1)

    def test_retry_then_fail_does_not_leak_key(self):
        import requests
        c, sess = self.mk([requests.ConnectionError("https://x/SECRETKEY/"), requests.ConnectionError("https://x/SECRETKEY/")])
        with self.assertRaises(fc.FirmsUnavailable) as cm:
            c.fetch_area("MODIS_SP", AOI, date(2024, 7, 1), 1)
        self.assertNotIn("SECRETKEY", str(cm.exception))
        self.assertEqual(sess.get.call_count, 2)

    def test_retry_recovers(self):
        c, _ = self.mk([FakeResp("", 503), FakeResp(MODIS_HEADER + "\n" + M_ROW)])
        self.assertEqual(len(c.fetch_area("MODIS_SP", AOI, date(2024, 7, 1), 1)), 1)

    def test_empty_answer_is_no_rows(self):
        c, _ = self.mk([FakeResp(MODIS_HEADER)])
        self.assertEqual(c.fetch_area("MODIS_SP", AOI, date(2024, 7, 1), 1), [])

    def test_availability(self):
        c, _ = self.mk([FakeResp("data_id,min_date,max_date\nMODIS_SP,2000-11-01,2025-03-31\nMODIS_NRT,2025-03-27,2026-01-02\n")])
        av = c.availability()
        self.assertEqual(av["MODIS_SP"], (date(2000, 11, 1), date(2025, 3, 31)))


class ImportTests(TestCase):
    def mk(self, rows_by_call, key="K"):
        c = fc.FirmsClient(map_key=key, session=mock.Mock(), retries=0, sleep=lambda s: None)
        c.session.get.side_effect = [FakeResp(r) if isinstance(r, str) else r for r in rows_by_call]
        return c

    def test_no_key_creates_labelled_job_and_no_data(self):
        job = importer.run_api_import(fc.FirmsClient(map_key=""), "MODIS_SP", AOI, date(2024, 7, 1), date(2024, 7, 3))
        self.assertEqual(job.status, FetchJob.Status.NO_KEY)
        self.assertEqual(Hotspot.objects.count(), 0)

    def test_api_import_stores_dedups_and_records_coverage(self):
        bad = "0,0,1,1,1,2024-07-10,99999,Terra,MODIS,50,6,1,1,D"
        body = "\n".join([MODIS_HEADER, M_ROW, M_ROW, bad, "x,y,1,1,1,2024-07-10,1030,Terra,MODIS,50,6,1,1,D"])
        c = self.mk([body, body])
        job = importer.run_api_import(c, "MODIS_NRT", AOI, date(2024, 7, 10), date(2024, 7, 10))
        self.assertEqual((job.status, job.rows_received, job.rows_created, job.rows_duplicate, job.rows_invalid), ("success", 4, 1, 1, 2))
        self.assertEqual(job.invalid_reasons, {"bad_time": 1, "missing_coordinate": 1})
        self.assertEqual(job.covered_ranges, [["2024-07-10", "2024-07-10"]])
        h = Hotspot.objects.get()
        self.assertEqual((h.product, h.fetch_id, h.is_demo, h.category), ("MODIS_NRT", job.id, False, "nasa_nrt"))
        # importing the same data again creates nothing new
        job2 = importer.run_api_import(self.mk([body]), "MODIS_NRT", AOI, date(2024, 7, 10), date(2024, 7, 10))
        self.assertEqual((job2.rows_created, job2.rows_duplicate), (0, 2))
        self.assertEqual(Hotspot.objects.count(), 1)

    def test_partial_failure_keeps_good_chunks(self):
        ok = MODIS_HEADER + "\n" + M_ROW
        c = self.mk([ok, FakeResp("", 500)])
        job = importer.run_api_import(c, "MODIS_NRT", AOI, date(2024, 7, 10), date(2024, 7, 19))
        self.assertEqual((job.status, job.days_ok, job.days_failed), ("partial", 5, 5))
        self.assertEqual(job.covered_ranges, [["2024-07-10", "2024-07-14"]])

    def test_invalid_key_fails_job_and_message_hides_key(self):
        job = importer.run_api_import(self.mk([FakeResp("", 403)], key="TOPSECRET"), "MODIS_SP", AOI, date(2024, 7, 1), date(2024, 7, 2))
        self.assertEqual(job.status, "failed")
        self.assertNotIn("TOPSECRET", job.message)

    def test_plan_ranges_splits_sp_and_nrt(self):
        av = {"MODIS_SP": (date(2000, 1, 1), date(2025, 3, 31)), "MODIS_NRT": (date(2025, 3, 27), date(2026, 1, 1))}
        plan = importer.plan_ranges("MODIS", date(2025, 3, 1), date(2025, 4, 10), av)
        self.assertEqual(plan, [("MODIS_SP", date(2025, 3, 1), date(2025, 3, 31)), ("MODIS_NRT", date(2025, 4, 1), date(2025, 4, 10))])
        self.assertEqual(importer.plan_ranges("MODIS", date(2030, 1, 1), date(2030, 1, 5), av), [])

    def test_csv_import_infers_coverage_and_rejects_bad_rows(self):
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as f:
            f.write("\n".join([VIIRS_HEADER, V_ROW, "95,0,1,1,1,2024-07-10,1031,N,VIIRS,n,2,1,1,D"]))
        job = importer.import_csv(f.name)
        self.assertEqual((job.sensor, job.rows_created, job.rows_invalid, job.coverage_declared), ("VIIRS", 1, 1, False))
        self.assertTrue(job.has_bbox)
        self.assertEqual(Hotspot.objects.get().category, "nasa_imported_csv")


def make(sensor, lat, lon, day, t, sat, product, frp=5.0, conf="n", **kw):
    return Hotspot.objects.create(sensor=sensor, satellite=sat, latitude=lat, longitude=lon, acq_date=day, acq_time=t,
                                  product=product, frp=frp, confidence=conf, instrument=sensor, **kw)


class HarmonizationTests(TestCase):
    D = date(2024, 7, 10)

    def test_nrt_copy_of_sp_is_flagged_not_deleted(self):
        sp = make("MODIS", 34.5, -5.5, self.D, 1030, "Terra", "MODIS_SP")
        nrt = make("MODIS", 34.5, -5.5, self.D, 1030, "Terra", "MODIS_NRT")
        other = make("MODIS", 34.5, -5.5, self.D, 1030, "Aqua", "MODIS_NRT")  # different platform: not a duplicate
        pipeline.run(self.D, self.D, "real")
        nrt.refresh_from_db(); sp.refresh_from_db(); other.refresh_from_db()
        self.assertEqual(nrt.exact_dup_of_id, sp.id)
        self.assertIsNone(sp.exact_dup_of_id); self.assertIsNone(other.exact_dup_of_id)
        self.assertEqual(Hotspot.objects.count(), 3)
        self.assertEqual(hotspots(scope="real").count(), 2)

    def test_overlap_pairs_modis_and_viirs_without_changing_values(self):
        m = make("MODIS", 34.5, -5.5, self.D, 1030, "Terra", "MODIS_SP", frp=12.5, scan=1.0, track=1.0)
        v = make("VIIRS", 34.5004, -5.5003, self.D, 1100, "N", "VIIRS_SNPP_SP", frp=3.0, scan=0.4, track=0.4)
        far = make("VIIRS", 34.7, -5.5, self.D, 1030, "N", "VIIRS_SNPP_SP")
        late = make("VIIRS", 34.5, -5.5, self.D, 2200, "N", "VIIRS_SNPP_SP")
        run = pipeline.run(self.D, self.D, "real")
        for o in (m, v, far, late):
            o.refresh_from_db()
        self.assertEqual(m.overlap_group, v.overlap_group)
        self.assertIsNotNone(m.overlap_group)
        self.assertIsNone(far.overlap_group); self.assertIsNone(late.overlap_group)
        self.assertEqual((m.frp, v.frp), (12.5, 3.0))
        self.assertEqual(run.stats["overlap_groups"], 1)

    def test_same_sensor_neighbours_never_group(self):
        a = make("VIIRS", 34.5, -5.5, self.D, 1030, "N", "VIIRS_SNPP_SP")
        b = make("VIIRS", 34.5001, -5.5, self.D, 1030, "N20", "VIIRS_NOAA20_SP")
        pipeline.run(self.D, self.D, "real")
        a.refresh_from_db(); self.assertIsNone(a.overlap_group)

    def test_idempotent_and_scopes_separate(self):
        make("MODIS", 34.5, -5.5, self.D, 1030, "Terra", "MODIS_SP")
        make("VIIRS", 34.5, -5.5, self.D, 1031, "N", "VIIRS_SNPP_SP")
        demo = Hotspot.objects.create(sensor="VIIRS", satellite="N", latitude=34.5, longitude=-5.5, acq_date=self.D, acq_time=1031, is_demo=True)
        pipeline.run(self.D, self.D, "real"); pipeline.run(self.D, self.D, "real")
        demo.refresh_from_db()
        self.assertIsNone(demo.overlap_group)  # demo rows never join real groups
        self.assertEqual(Hotspot.objects.filter(overlap_group__isnull=False).count(), 2)
        with self.assertRaises(ValueError):
            pipeline.run(self.D, self.D, "all")

    def test_unique_constraint_blocks_real_duplicates(self):
        make("MODIS", 34.5, -5.5, self.D, 1030, "Terra", "MODIS_SP")
        from django.db import IntegrityError, transaction
        with self.assertRaises(IntegrityError), transaction.atomic():
            make("MODIS", 34.5, -5.5, self.D, 1030, "Terra", "MODIS_SP")

    def test_timestamp_minutes(self):
        self.assertEqual(pipeline.obs_minutes(date(1970, 1, 2), 130), 1440 + 90)
        self.assertEqual(pipeline.obs_minutes(date(1970, 1, 1), 2359), 23 * 60 + 59)

    def test_empty_dataset(self):
        self.assertEqual(pipeline.find_overlaps([])[0], {})
        self.assertEqual(pipeline.find_exact_duplicates([]), {})


class CalendarTests(TestCase):
    def setUp(self):
        self.start = date(2024, 6, 1)
        job = FetchJob.objects.create(kind="api", product="MODIS_SP", sensor="MODIS", south=30, west=-10, north=36, east=-1,
                                      start_date=self.start, end_date=date(2024, 6, 30), status="success",
                                      covered_ranges=[["2024-06-01", "2024-06-30"]])
        for d in range(0, 29):  # 1..29 June: quiet baseline of 2 detections/day
            for k in range(2):
                make("MODIS", 33 + k * .1, -5 + d * .001, self.start + timedelta(days=d), 1030, "Terra", "MODIS_SP", fetch=job)
        for k in range(40):  # 30 June: spike
            make("MODIS", 34 + k * .01, -5, date(2024, 6, 30), 1030, "Terra", "MODIS_SP", fetch=job)

    def filters(self, **kw):
        return common_filters({"south": "30", "west": "-10", "north": "36", "east": "-1", **kw})

    def test_status_distinguishes_no_data_zero_and_detections(self):
        job = FetchJob.objects.get()
        job.covered_ranges = [["2024-06-01", "2024-07-05"]]; job.end_date = date(2024, 7, 5); job.save()
        c = cal.build_calendar(self.filters(), date(2024, 6, 29), date(2024, 7, 8))
        st = {d["date"]: d["status"] for d in c["days"]}
        self.assertEqual(st["2024-06-29"], "detections")
        self.assertEqual(st["2024-07-02"], "zero")      # queried, nothing found
        self.assertEqual(st["2024-07-07"], "no_data")   # never queried: NOT zero
        self.assertEqual(c["summary"]["zero_days"] + c["summary"]["no_data_days"] + c["summary"]["active_days"], 10)

    def test_counts_match_rows_and_flag_spike(self):
        c = cal.build_calendar(self.filters(), date(2024, 6, 1), date(2024, 6, 30))
        self.assertEqual(c["summary"]["modis"], Hotspot.objects.count())
        self.assertEqual(c["summary"]["viirs"], 0)
        last = c["days"][-1]
        self.assertTrue(last["high"]); self.assertEqual(last["total"], 40)
        self.assertFalse(any(d["high"] for d in c["days"][:-1]))
        self.assertEqual(c["months"]["2024-06"]["high_days"], 1)

    def test_aoi_not_covered_means_no_data(self):
        f = common_filters({"south": "10", "west": "-10", "north": "36", "east": "-1"})  # larger than the import area
        c = cal.build_calendar(f, date(2024, 6, 1), date(2024, 6, 3))
        self.assertEqual(c["summary"]["no_data_days"] + c["summary"]["active_days"], 3)

    def test_short_history_has_no_flags(self):
        c = cal.build_calendar(self.filters(), date(2024, 6, 25), date(2024, 6, 30))
        self.assertFalse(any(d["high"] for d in c["days"]))

    def test_sensor_filter_and_empty_range(self):
        c = cal.build_calendar(self.filters(sensor="VIIRS"), date(2024, 6, 1), date(2024, 6, 5))
        self.assertEqual(c["summary"]["modis"] + c["summary"]["viirs"], 0)
        self.assertEqual(c["summary"]["no_data_days"], 5)  # VIIRS was never imported

    def test_compare_periods(self):
        d = cal.compare_periods(self.filters(), date(2024, 6, 1), date(2024, 6, 10), date(2024, 6, 11), date(2024, 6, 20))
        self.assertEqual((d["a"]["summary"]["modis"], d["b"]["summary"]["modis"]), (20, 20))
        self.assertEqual(d["change"]["modis"], 0.0)

    def test_coverage_merge(self):
        r = cov.coverage_ranges((31, -9, 35, -2), "real")
        self.assertEqual(r["MODIS"], [(date(2024, 6, 1), date(2024, 6, 30))])
        self.assertEqual(r["VIIRS"], [])
        self.assertEqual(cov.coverage_ranges((31, -9, 35, -2), "demo")["MODIS"], [])


class SpatialTests(TestCase):
    def test_st_dbscan_groups_space_time(self):
        pts = [(i, 34 + i * 0.002, -5, 1000 + i * 30) for i in range(5)]            # one tight cluster
        pts += [(10 + i, 34 + i * 0.002, -5, 1000 + 5 * 1440 + i * 30) for i in range(4)]  # same place 5 days later
        pts += [(20, 36, -2, 1000)]                                                 # isolated
        labels, n = spatial.st_dbscan(pts, eps_km=2, eps_hours=24, min_pts=3)
        self.assertEqual(n, 2)
        self.assertEqual(labels[20], -1)
        self.assertNotEqual(labels[0], labels[10])

    def test_cluster_limit_message(self):
        with mock.patch.object(spatial, "MAX_CLUSTER_POINTS", 1):
            make("MODIS", 34, -5, date(2024, 1, 1), 1, "Terra", "MODIS_SP"); make("MODIS", 35, -5, date(2024, 1, 1), 1, "Terra", "MODIS_SP")
            with self.assertRaises(ValueError):
                spatial.clusters(hotspots(scope="real"))

    def test_empty(self):
        self.assertEqual(spatial.clusters(Hotspot.objects.none())["n_clusters"], 0)
        self.assertEqual(spatial.density(Hotspot.objects.none())["cells"], [])


class ViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        make("MODIS", 34.5, -5.5, date(2024, 7, 10), 1030, "Terra", "MODIS_SP", frp=10, brightness=330.0, confidence_raw="85", conf="h")
        make("VIIRS", 34.5, -5.5, date(2024, 7, 10), 1031, "N", "VIIRS_SNPP_SP", frp=3, conf="n")
        make("VIIRS", 34.6, -5.5, date(2024, 7, 10), 1031, "N", "VIIRS_SNPP_SP", conf="")
        FetchJob.objects.create(kind="api", product="MODIS_SP", sensor="MODIS", south=20, west=-18, north=36, east=-1,
                                status="success", covered_ranges=[["2024-07-01", "2024-07-31"]])
        U = get_user_model()
        cls.operator = U.objects.create_user("op", "op@x.com", "pw-12345-abc")
        cls.operator.profile.role = "operator"; cls.operator.profile.save()
        cls.member = U.objects.create_user("mem", "mem@x.com", "pw-12345-abc")

    def get(self, name, **q):
        return self.client.get(reverse(f"satellite:{name}"), q)

    def test_all_pages_render(self):
        for n in ("overview", "map", "harmonization", "history", "data"):
            r = self.client.get(reverse(f"sat:{n}"))
            self.assertEqual(r.status_code, 200, n)
            self.assertContains(r, "Satellite")
        self.assertEqual(self.client.get(reverse("public_dashboard")).status_code, 200)
        self.assertNotContains(self.client.get(reverse("sat:data")), "FIRMS_MAP_KEY=")  # key never rendered

    def test_overview_counts_and_labels(self):
        d = self.get("overview", start="2024-07-01", end="2024-07-31", min_conf="all").json()
        self.assertEqual((d["total"], d["modis"], d["viirs"]), (3, 1, 2))
        self.assertEqual(d["scope"], "real")
        self.assertIn("nasa_standard", d["categories"])
        self.assertEqual(d["quality"]["missing_confidence"], 1)

    def test_confidence_filter_default_excludes_unknown(self):
        self.assertEqual(self.get("detections", min_conf="n").json()["total"], 2)
        self.assertEqual(self.get("detections", min_conf="all").json()["total"], 3)
        self.assertEqual(self.get("detections", min_conf="h").json()["total"], 1)

    def test_geographic_temporal_and_sensor_filters(self):
        self.assertEqual(self.get("detections", min_conf="all", south=34.55, north=35, west=-6, east=-5).json()["total"], 1)
        self.assertEqual(self.get("detections", min_conf="all", start="2024-07-11").json()["total"], 0)
        self.assertEqual(self.get("detections", min_conf="all", sensor="MODIS").json()["total"], 1)

    def test_detection_only_lists_present_fields(self):
        g = self.get("detections", format="geojson", sensor="VIIRS", min_conf="n").json()
        p = g["features"][0]["properties"]
        self.assertNotIn("brightness_k", p); self.assertEqual(p["category"], "nasa_standard")
        m = self.get("detections", format="geojson", sensor="MODIS").json()["features"][0]["properties"]
        self.assertEqual((m["brightness_k"], m["confidence"]), (330.0, "85"))

    def test_bad_requests_are_400(self):
        for q in ({"south": "abc"}, {"south": "40", "north": "30"}, {"start": "2024-13-01"}, {"min_conf": "x"}, {"sensor": "LANDSAT"},
                  {"scope": "x"}, {"start": "2024-08-01", "end": "2024-07-01"}):
            self.assertEqual(self.get("overview", **q).status_code, 400, q)
        self.assertEqual(self.get("day").status_code, 400)
        self.assertEqual(self.get("clusters", eps_km="99").status_code, 400)
        self.assertEqual(self.get("compare", start="2024-01-01").status_code, 400)

    def test_demo_never_mixes_into_real_by_default(self):
        Hotspot.objects.create(sensor="MODIS", satellite="Terra", latitude=34.5, longitude=-5.5, acq_date=date(2024, 7, 10), acq_time=1, is_demo=True)
        self.assertEqual(self.get("detections", min_conf="all").json()["total"], 3)
        self.assertEqual(self.get("detections", min_conf="all", scope="demo").json()["total"], 1)
        self.assertEqual(self.get("detections", min_conf="all", scope="all").json()["total"], 4)
        self.assertEqual(self.get("overview", scope="demo").json()["categories"], {"synthetic_demo": 1})

    def test_day_detail_and_calendar(self):
        d = self.get("day", date="2024-07-10", min_conf="all").json()
        self.assertEqual((d["status"], d["total"]), ("detections", 3))
        z = self.get("day", date="2024-07-12", min_conf="all").json()
        self.assertEqual((z["status"], z["total"]), ("zero", 0))
        n = self.get("day", date="2024-08-12", min_conf="all").json()
        self.assertEqual(n["status"], "no_data")

    def test_harmonization_and_other_endpoints(self):
        for name in ("harmonization", "spatial", "temporal", "imports", "summary", "cells"):
            self.assertEqual(self.get(name, min_conf="all").status_code, 200, name)
        self.assertEqual(self.get("anomalies", start="2024-07-01", end="2024-07-31").status_code, 200)

    def test_empty_database(self):
        Hotspot.objects.all().delete()
        d = self.get("overview").json()
        self.assertEqual((d["total"], d["scope"]), (0, "empty"))
        self.assertEqual(self.get("summary").json()["has_data"], False)
        self.assertEqual(self.get("calendar").status_code, 400)  # no data span to default to

    def test_export_csv(self):
        r = self.get("export", min_conf="all")
        rows = b"".join(r.streaming_content).decode().strip().splitlines()
        self.assertEqual(len(rows), 4)
        self.assertIn("data_category", rows[0]); self.assertIn("nasa_standard", rows[1])

    def test_post_only_for_operators(self):
        url = reverse("sat:start_import")
        self.assertEqual(self.client.post(url, {}).status_code, 302)  # anonymous -> login
        self.client.force_login(self.member)
        self.assertIn(self.client.post(url, {}).status_code, (302, 403))
        self.assertEqual(self.client.get(reverse("sat:key_status")).status_code in (302, 403), True)
        self.client.force_login(self.operator)
        self.assertEqual(self.client.get(url).status_code, 405)

    @override_settings(FIRMS_MAP_KEY="")
    def test_operator_import_without_key_does_nothing_and_says_so(self):
        self.client.force_login(self.operator)
        n = FetchJob.objects.count()
        r = self.client.post(reverse("sat:start_import"), {"families": ["MODIS"], "south": 30, "west": -10, "north": 36, "east": -1,
                                                           "start": "2024-07-01", "end": "2024-07-03"}, follow=True)
        self.assertContains(r, "No FIRMS_MAP_KEY")
        self.assertEqual(FetchJob.objects.count(), n)
        self.assertEqual(self.client.get(reverse("sat:key_status")).json()["configured"], False)
        self.assertContains(self.client.get(reverse("sat:overview")), "MAP_KEY")

    @override_settings(FIRMS_MAP_KEY="abc")
    def test_import_form_validation(self):
        self.client.force_login(self.operator)
        r = self.client.post(reverse("sat:start_import"), {"families": ["MODIS"], "south": 36, "west": -10, "north": 30, "east": -1,
                                                           "start": "2024-07-01", "end": "2024-07-03"}, follow=True)
        self.assertContains(r, "South must be smaller than north")
        r = self.client.post(reverse("sat:start_import"), {"families": ["MODIS"], "south": 30, "west": -10, "north": 36, "east": -1,
                                                           "start": "2020-01-01", "end": "2024-07-03"}, follow=True)
        self.assertContains(r, "at most 366 days")
