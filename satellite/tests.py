from datetime import date, timedelta

from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from . import demo
from . import harmonize as H
from .models import Hotspot


def rec(sensor, day, lat=32.0, lon=-6.0, frp=5.0):
    return (sensor, day, lat, lon, frp)


class HarmonizeUnitTests(SimpleTestCase):
    def test_same_cell_counts_once_per_day(self):
        d = date(2020, 7, 1)
        occ = H.cell_days([rec("MODIS", d, 32.02, -5.98), rec("MODIS", d, 32.02, -5.98), rec("MODIS", d, 32.03, -5.97), rec("MODIS", d, 33.02, -5.98)])
        self.assertEqual(H.daily_counts(occ["MODIS"])[d], 2)

    def test_viirs_multiple_pixels_do_not_inflate(self):
        d = date(2020, 7, 1)
        occ = H.cell_days([rec("VIIRS", d, 32.02 + i * 0.002, -5.98) for i in range(4)])
        self.assertEqual(H.daily_counts(occ["VIIRS"])[d], 1)

    def test_scale_is_ratio_on_overlap(self):
        start = date(2020, 1, 1)
        m = {start + timedelta(i): 2 for i in range(100)}
        v = {start + timedelta(i): 4 for i in range(100)}
        cov = {"MODIS": (start, start + timedelta(99)), "VIIRS": (start, start + timedelta(99))}
        cal = H.estimate_scale(m, v, cov)
        self.assertAlmostEqual(cal["scale"], 0.5)
        self.assertEqual(cal["method"], "area-overlap")

    def test_scale_falls_back_when_overlap_too_small(self):
        start = date(2020, 1, 1)
        cov = {"MODIS": (start, start + timedelta(9)), "VIIRS": (start, start + timedelta(9))}
        cal = H.estimate_scale({start: 1}, {start: 2}, cov, fallback=0.7)
        self.assertEqual((cal["scale"], cal["method"]), (0.7, "fallback"))
        self.assertEqual(H.estimate_scale({start: 1}, {start: 2}, cov)["method"], "insufficient-overlap")

    def test_single_sensor_scale_is_one(self):
        cal = H.estimate_scale({}, {}, {"MODIS": (date(2020, 1, 1), date(2020, 2, 1))})
        self.assertEqual((cal["scale"], cal["method"]), (1.0, "single-sensor"))

    def test_series_is_continuous_across_viirs_start(self):
        """Same real burning seen by both sensors: harmonized stays flat after VIIRS starts."""
        start = date(2010, 1, 1)
        modis = {start + timedelta(i): 10 for i in range(1000)}
        viirs = {start + timedelta(i): 25 for i in range(500, 1000)}  # 2.5x more cells
        cov = {"MODIS": (start, start + timedelta(999)), "VIIRS": (start + timedelta(500), start + timedelta(999))}
        cal = H.estimate_scale(modis, viirs, cov)
        series = H.harmonize(modis, viirs, cov, cal["scale"])
        before = series[start + timedelta(100)][0]
        after = series[start + timedelta(800)][0]
        self.assertAlmostEqual(before, after)

    def test_anomaly_uses_leave_one_out_baseline(self):
        matrix = {y: {10: 6.0} for y in range(2010, 2016)}
        matrix[2016] = {10: 60.0}
        found = H.anomalies(matrix)
        self.assertEqual([a["year"] for a in found], [2016])
        self.assertEqual(found[0]["level"], "extreme")

    def test_no_anomaly_without_enough_years(self):
        self.assertEqual(H.anomalies({2010: {5: 50.0}, 2011: {5: 1.0}}), [])

    def test_critical_period_merges_adjacent_weeks(self):
        clim = [{"bin": b, "start_doy": b * 7 + 1, "mean": 0.0, "std": 0.0, "n": 10} for b in range(52)]
        for b in (25, 26, 27):
            clim[b]["mean"] = 10.0
        periods = H.critical_periods(clim)
        self.assertEqual(len(periods), 1)
        self.assertEqual(periods[0]["weeks"], 3)

    def test_demo_data_reproduces_the_viirs_jump(self):
        rows = list(demo.generate(seed=3, start_year=2010, end=date(2015, 12, 31)))
        records = [(r["sensor"], r["acq_date"], r["latitude"], r["longitude"], r["frp"]) for r in rows]
        cov = {s: (min(r["acq_date"] for r in rows if r["sensor"] == s),
                   max(r["acq_date"] for r in rows if r["sensor"] == s)) for s in ("MODIS", "VIIRS")}
        summary = H.build_summary(records, cov)
        raw_modis = sum(sum(m["modis"]) for m in summary["monthly"].values())
        raw_viirs = sum(sum(m["viirs"]) for m in summary["monthly"].values())
        self.assertGreater(raw_viirs, 0)
        self.assertEqual(summary["harmonization"]["method"], "area-overlap")
        self.assertGreater(summary["harmonization"]["r_weekly"], 0.8)
        self.assertGreater(raw_modis, 0)


class FireApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        base = date(2020, 6, 1)
        rows = []
        for i in range(120):
            d = base + timedelta(days=i)
            for k in range(3):
                rows.append(Hotspot(sensor="MODIS", latitude=32.0 + k * 0.1, longitude=-6.0, acq_date=d, frp=4, confidence="n"))
                rows.append(Hotspot(sensor="VIIRS", latitude=32.0 + k * 0.1, longitude=-6.0, acq_date=d, frp=3, confidence="h"))
        rows.append(Hotspot(sensor="VIIRS", latitude=32.0, longitude=-6.0, acq_date=base, confidence="l"))
        rows.append(Hotspot(sensor="MODIS", latitude=10.0, longitude=10.0, acq_date=base, confidence="h"))  # outside Morocco
        Hotspot.objects.bulk_create(rows)

    def test_summary_shape(self):
        r = self.client.get(reverse("satellite:summary"))
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertTrue(data["has_data"])
        self.assertEqual(data["data_mode"], "real")
        self.assertIn("2020", data["daily"])
        self.assertEqual(data["harmonization"]["method"], "area-overlap")
        self.assertAlmostEqual(data["harmonization"]["scale"], 1.0, places=2)

    def test_summary_is_public(self):
        self.assertEqual(self.client.get(reverse("satellite:summary")).status_code, 200)

    def test_bbox_excludes_points_outside(self):
        data = self.client.get(reverse("satellite:summary"), {"south": 31, "west": -7, "north": 33, "east": -5}).json()
        total_modis = sum(sum(m["modis"]) for m in data["monthly"].values())
        self.assertEqual(total_modis, 120 * 3)

    def test_low_confidence_excluded_by_default(self):
        qs = {"south": 31.9, "west": -6.1, "north": 32.05, "east": -5.9}
        default = self.client.get(reverse("satellite:cells"), {**qs, "start": "2020-06-01", "end": "2020-06-01"}).json()
        with_low = self.client.get(reverse("satellite:cells"), {**qs, "start": "2020-06-01", "end": "2020-06-01", "min_conf": "l"}).json()
        self.assertEqual(default["cells"][0]["sensors"], "MV")
        self.assertGreaterEqual(with_low["cells"][0]["frp"], default["cells"][0]["frp"])

    def test_cells_for_single_day(self):
        data = self.client.get(reverse("satellite:cells"), {"start": "2020-06-01", "end": "2020-06-01"}).json()
        self.assertEqual(len(data["cells"]), 3)
        self.assertTrue(all(c["days"] == 1 for c in data["cells"]))

    def test_bad_input_returns_400(self):
        for params in ({"south": "abc"}, {"south": 40, "north": 30}, {"west": 10, "east": 5}, {"min_conf": "x"}):
            with self.subTest(params=params):
                self.assertEqual(self.client.get(reverse("satellite:summary"), params).status_code, 400)
        self.assertEqual(self.client.get(reverse("satellite:cells"), {"start": "nope"}).status_code, 400)
        self.assertEqual(self.client.get(reverse("satellite:cells"), {"start": "2021-01-01", "end": "2020-01-01"}).status_code, 400)

    def test_api_is_read_only(self):
        self.assertEqual(self.client.post(reverse("satellite:summary")).status_code, 405)


class EmptyDatabaseTests(TestCase):
    def test_summary_reports_no_data(self):
        data = self.client.get(reverse("satellite:summary")).json()
        self.assertFalse(data["has_data"])
        self.assertEqual(data["data_mode"], "empty")
