"""Pure tests (plain unittest, no Django, no database, no network): estimates, A/B/C/D classification, sync policy.

Run anywhere:  python -m unittest satellite.test_estimate -v
"""

import unittest
from datetime import date, datetime, timedelta

from satellite.analysis import estimate as E
from satellite.services import sync_policy as P

TODAY = date(2026, 11, 14)


def years(n, counts_per_day=1, days=15, start=2010):
    return {start + i: [counts_per_day] * days for i in range(n)}


class PercentileTests(unittest.TestCase):
    def test_interpolation(self):
        self.assertEqual(E.percentile([1, 2, 3, 4, 5], 50), 3.0)
        self.assertAlmostEqual(E.percentile([0, 10], 10), 1.0)
        self.assertEqual(E.percentile([7], 90), 7.0)

    def test_empty_raises(self):
        with self.assertRaises(ValueError):
            E.percentile([], 50)

    def test_wilson_bounds(self):
        lo, hi = E.wilson(5, 10)
        self.assertTrue(0 < lo < 0.5 < hi < 1)
        self.assertEqual(E.wilson(0, 0), (None, None))
        lo, hi = E.wilson(0, 10)
        self.assertEqual(lo, 0.0)
        self.assertGreater(hi, 0.0)


class EstimateTests(unittest.TestCase):
    def test_insufficient_years_refuses(self):
        r = E.estimate(years(4))
        self.assertEqual(r["status"], "insufficient")
        self.assertIn("reliable estimate cannot be generated", r["reason"])
        self.assertNotIn("indicators", r)

    def test_empty_history_refuses(self):
        r = E.estimate({})
        self.assertEqual(r["status"], "insufficient")
        self.assertEqual(r["n_years"], 0)
        self.assertIsNone(r["reference_period"])

    def test_estimate_carries_all_required_fields(self):
        daily = {2015 + i: [i] * 15 for i in range(6)}          # yearly means 0..5
        r = E.estimate(daily)
        self.assertEqual(r["status"], "estimate")
        for key in ("methodology", "reference_period", "n_years", "n_detections", "indicators", "uncertainty", "limitations"):
            self.assertTrue(r[key] not in (None, "", [], {}), key)
        self.assertEqual(r["reference_period"], [2015, 2020])
        self.assertEqual(r["n_detections"], 15 * (0 + 1 + 2 + 3 + 4 + 5))
        ind = r["indicators"]
        self.assertAlmostEqual(ind["mean_detections_per_day"], 2.5)
        self.assertAlmostEqual(ind["median_detections_per_day"], 2.5)
        self.assertEqual(ind["years_with_any_detection"], 5)
        self.assertAlmostEqual(ind["share_of_years_with_detection"], 5 / 6, places=3)
        self.assertLess(ind["p10_detections_per_day"], ind["p90_detections_per_day"])
        self.assertTrue(any("not an observation" in s for s in r["limitations"]))

    def test_all_zero_history_is_an_estimate_of_zero_not_a_fire(self):
        r = E.estimate(years(5, counts_per_day=0))
        self.assertEqual(r["status"], "estimate")
        self.assertEqual(r["indicators"]["mean_detections_per_day"], 0)
        self.assertEqual(r["indicators"]["share_of_years_with_detection"], 0)
        self.assertIsNone(r["indicators"]["coefficient_of_variation"])
        self.assertEqual(r["indicators"]["share_of_years_with_detection_95ci"][0], 0.0)

    def test_leap_day_falls_back(self):
        self.assertEqual(E.same_day_in_year(date(2024, 2, 29), 2023), date(2023, 2, 28))
        self.assertEqual(E.same_day_in_year(date(2024, 2, 29), 2020), date(2020, 2, 29))


def sensor(detections=0, covered=False, daily=None, exact=None, observed=None):
    return {"detections": detections, "covered": covered, "daily_by_year": daily or {}, "exact_by_year": exact or {},
            "observed": observed or {}}


class ClassificationTests(unittest.TestCase):
    def payload(self, sensors, target=TODAY):
        return E.build_today_payload(target, TODAY, sensors)

    def test_observations_are_category_a_and_have_no_estimate(self):
        p = self.payload({"MODIS": sensor(detections=7, covered=True, daily=years(8)),
                          "VIIRS": sensor(detections=0, covered=True)})
        by = {s["sensor"]: s for s in p["sensors"]}
        self.assertEqual(by["MODIS"]["category"], "A")
        self.assertEqual(by["MODIS"]["detections"], 7)
        self.assertNotIn("estimate", by["MODIS"])        # real data is never replaced or shadowed by an estimate
        self.assertTrue(by["VIIRS"]["verified_zero"])    # a queried-and-empty day is a verified zero, not an estimate
        self.assertEqual(p["primary_category"], "A")

    def test_uncovered_today_with_history_is_category_c(self):
        p = self.payload({"VIIRS": sensor(daily=years(6, 2), exact={2020: 3, 2021: 0})})
        s = p["sensors"][0]
        self.assertEqual(s["category"], "C")
        self.assertEqual(s["estimate"]["status"], "estimate")
        self.assertNotIn("detections", s)                # an estimate carries no detection count
        self.assertIn("not a satellite detection", s["label"])
        self.assertEqual([h["year"] for h in s["historical"]], [2020, 2021])
        self.assertEqual(p["primary_category"], "C")
        self.assertTrue(p["has_historical"])

    def test_not_enough_history_is_category_d_with_reason(self):
        p = self.payload({"MODIS": sensor(daily=years(2), exact={2019: 1})})
        s = p["sensors"][0]
        self.assertEqual(s["category"], "D")
        self.assertIn("cannot be generated", s["reason"])
        self.assertEqual(p["primary_category"], "D")
        self.assertEqual(s["historical"][0]["detections"], 1)   # historical observations stay visible as B

    def test_future_date_is_unavailable_not_estimated(self):
        p = self.payload({"MODIS": sensor(daily=years(9))}, target=TODAY + timedelta(days=3))
        self.assertTrue(p["is_future"])
        self.assertEqual(p["sensors"][0]["category"], "D")
        self.assertNotIn("estimate", p["sensors"][0])

    def test_transition_estimate_to_verified(self):
        """The scenario of the brief: estimate first, real observations after the next synchronisation."""
        before = self.payload({"VIIRS": sensor(daily=years(7))})
        after = self.payload({"VIIRS": sensor(detections=4, covered=True, daily=years(7),
                                              observed={"latest_acq_utc": "2026-11-14T10:31:00+00:00"})})
        self.assertEqual(before["primary_category"], "C")
        self.assertEqual(after["primary_category"], "A")
        self.assertEqual(after["sensors"][0]["detections"], 4)
        self.assertNotIn("estimate", after["sensors"][0])

    def test_best_category_across_sensors(self):
        p = self.payload({"MODIS": sensor(daily=years(1)), "VIIRS": sensor(daily=years(6))})
        self.assertEqual(p["primary_category"], "C")
        p = self.payload({})
        self.assertEqual(p["primary_category"], "D")

    def test_labels_are_the_four_required_categories(self):
        self.assertEqual(set(E.LABELS), {"A", "B", "C", "D"})
        self.assertIn("VERIFIED NASA OBSERVATIONS", E.LABELS["A"])
        self.assertIn("HISTORICAL OBSERVATIONS", E.LABELS["B"])
        self.assertIn("STATISTICAL ESTIMATE", E.LABELS["C"])
        self.assertIn("UNAVAILABLE", E.LABELS["D"])


class SyncPolicyTests(unittest.TestCase):
    def avail(self, **newest):
        out = {}
        for fam, d in newest.items():
            out[f"{fam}_NRT"] = (date(2026, 6, 1), d)
        return out

    def test_empty_availability_skips_nothing(self):
        self.assertEqual(P.active_families(["MODIS", "VIIRS_SNPP"], {}, TODAY), (["MODIS", "VIIRS_SNPP"], {}))

    def test_stale_family_is_skipped_with_reason(self):
        av = self.avail(MODIS=TODAY, VIIRS_SNPP=date(2026, 11, 2), VIIRS_NOAA20=TODAY - timedelta(days=1))
        active, skipped = P.active_families(["MODIS", "VIIRS_SNPP", "VIIRS_NOAA20", "VIIRS_NOAA21"], av, TODAY)
        self.assertEqual(active, ["MODIS", "VIIRS_NOAA20"])
        self.assertIn("VIIRS_SNPP", skipped)
        self.assertIn("2026-11-02", skipped["VIIRS_SNPP"])
        self.assertIn("VIIRS_NOAA21", skipped)           # not listed at all

    def test_family_with_recent_sp_only_is_kept(self):
        av = {"MODIS_SP": (date(2000, 11, 1), TODAY - timedelta(days=2))}
        self.assertEqual(P.active_families(["MODIS"], av, TODAY)[0], ["MODIS"])

    def test_choose_days(self):
        now = datetime(2026, 11, 14, 12, 0)
        self.assertEqual(P.choose_days(None, now), 3)
        self.assertEqual(P.choose_days(now - timedelta(minutes=10), now), 2)
        self.assertEqual(P.choose_days(now - timedelta(days=1, hours=2), now), 3)
        self.assertEqual(P.choose_days(now - timedelta(days=30), now), 10)      # capped
        self.assertGreaterEqual(P.choose_days(now - timedelta(hours=20), now), 2)

    def test_next_check(self):
        t = datetime(2026, 11, 14, 12, 0)
        self.assertEqual(P.next_check(t, 600), t + timedelta(minutes=10))
        self.assertIsNone(P.next_check(None, 600))


if __name__ == "__main__":
    unittest.main()
