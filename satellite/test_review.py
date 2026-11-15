"""Review tests: Gemini 429 fallback and model identifiers, FIRMS command parameters, heuristic labelling,
and strict real / synthetic separation.

All external services are MOCKED here. Nothing in this file contacts NASA FIRMS or Google Gemini, so none of
it proves that the real services accept these requests; see README "Validation status".
"""

import io
import json
from datetime import date, timedelta
from unittest import mock
from urllib.error import HTTPError, URLError

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.urls import reverse

from satellite import ai_assistant, ai_retrieval
from satellite.analysis.params import common_filters, hotspots
from satellite.harmonization import pipeline
from satellite.models import FetchJob, Hotspot

D = date(2024, 7, 10)
OK = {"candidates": [{"content": {"parts": [{"text": "answer"}]}}]}


def http_error(code):
    return HTTPError("https://x", code, "err", {}, io.BytesIO(b""))


def make(sensor, lat, lon, t, sat, product, day=D, **kw):
    kw.setdefault("confidence", "n")
    return Hotspot.objects.create(sensor=sensor, satellite=sat, latitude=lat, longitude=lon, acq_date=day, acq_time=t,
                                  product=product, instrument=sensor, **kw)


def demo(sensor, lat, lon, t, day=D, **kw):
    return Hotspot.objects.create(sensor=sensor, satellite="Terra" if sensor == "MODIS" else "N", latitude=lat, longitude=lon,
                                  acq_date=day, acq_time=t, is_demo=True, instrument=sensor, **kw)


class Gemini429Tests(TestCase):
    """Primary 429 -> next model; every model 429 -> graceful rate-limit answer; bounded attempts."""

    def run_q(self, behaviour):
        calls = []

        def fake(model, key, payload, timeout=40):
            calls.append(model)
            result = behaviour(model)
            if isinstance(result, Exception):
                raise result
            return result
        with mock.patch.object(ai_assistant, "_call_gemini", fake), mock.patch("time.sleep") as sleep:
            r = ai_assistant.answer_question("latest observations")
        return r, calls, sleep

    @override_settings(GEMINI_API_KEY="k", GEMINI_MODEL="a", GEMINI_FALLBACK_MODELS=["b", "c"])
    def test_primary_429_falls_back_to_next_model(self):
        r, calls, sleep = self.run_q(lambda m: http_error(429) if m == "a" else OK)
        self.assertEqual(calls, ["a", "b"])                 # one attempt on a, no retry of a, stops at first success
        self.assertEqual((r["mode"], r["answer"]), ("gemini", "answer"))
        sleep.assert_not_called()                           # no waiting on a quota error

    @override_settings(GEMINI_API_KEY="k", GEMINI_MODEL="a", GEMINI_FALLBACK_MODELS=["b", "c"])
    def test_second_model_429_then_third_succeeds(self):
        r, calls, _ = self.run_q(lambda m: OK if m == "c" else http_error(429))
        self.assertEqual((calls, r["mode"]), (["a", "b", "c"], "gemini"))

    @override_settings(GEMINI_API_KEY="k", GEMINI_MODEL="a", GEMINI_FALLBACK_MODELS=["b", "c"])
    def test_all_models_rate_limited(self):
        r, calls, sleep = self.run_q(lambda m: http_error(429))
        self.assertEqual(calls, ["a", "b", "c"])            # each model exactly once: no infinite retry
        self.assertEqual((r["mode"], r["reason"]), ("database_only", "assistant_rate_limited"))
        self.assertIn("every configured model", r["notice"])
        self.assertIn("Real NASA observations", r["answer"])  # still answers from the database
        sleep.assert_not_called()

    @override_settings(GEMINI_API_KEY="k", GEMINI_MODEL="a", GEMINI_FALLBACK_MODELS=["b", "c", "d", "e", "f"])
    def test_attempts_are_capped(self):
        _, calls, _ = self.run_q(lambda m: http_error(429))
        self.assertLessEqual(len(calls), ai_assistant.MAX_MODELS)

    @override_settings(GEMINI_API_KEY="k", GEMINI_MODEL="a", GEMINI_FALLBACK_MODELS=["b"])
    def test_single_model_429(self):
        with override_settings(GEMINI_FALLBACK_MODELS=[]):
            r, calls, _ = self.run_q(lambda m: http_error(429))
        self.assertEqual((calls, r["reason"]), (["a"], "assistant_rate_limited"))

    @override_settings(GEMINI_API_KEY="k", GEMINI_MODEL="a", GEMINI_FALLBACK_MODELS=["b"])
    def test_auth_error_stops_immediately(self):
        r, calls, _ = self.run_q(lambda m: http_error(403))
        self.assertEqual((calls, r["reason"]), (["a"], "assistant_auth_error"))

    @override_settings(GEMINI_API_KEY="k", GEMINI_MODEL="a", GEMINI_FALLBACK_MODELS=["b"])
    def test_429_then_server_error_reports_unavailable_not_rate_limit(self):
        r, calls, _ = self.run_q(lambda m: http_error(429) if m == "a" else http_error(503))
        self.assertEqual(r["reason"], "assistant_provider_unavailable")

    @override_settings(GEMINI_API_KEY="k", GEMINI_MODEL="a", GEMINI_FALLBACK_MODELS=["b"])
    def test_unknown_model_404_moves_on(self):
        r, calls, _ = self.run_q(lambda m: http_error(404) if m == "a" else OK)
        self.assertEqual((calls, r["mode"]), (["a", "b"], "gemini"))

    @override_settings(GEMINI_API_KEY="k", GEMINI_MODEL="a", GEMINI_FALLBACK_MODELS=["b"])
    def test_time_budget_stops_further_attempts(self):
        ticks = iter([0, 1000, 1000, 1000, 1000])
        with mock.patch("satellite.ai_assistant.time.monotonic", lambda: next(ticks)):
            r, calls, _ = self.run_q(lambda m: http_error(429))
        self.assertEqual(calls, ["a"])
        self.assertEqual(r["reason"], "assistant_rate_limited")

    @override_settings(GEMINI_API_KEY="k", GEMINI_MODEL="a", GEMINI_FALLBACK_MODELS=["b"])
    def test_chat_endpoint_returns_200_with_notice_when_all_rate_limited(self):
        self.client.force_login(get_user_model().objects.create_user("u", "u@x.com", "pw-12345-abc"))
        with mock.patch.object(ai_assistant, "_call_gemini", side_effect=http_error(429)):
            r = self.client.post(reverse("assistant_chat"), json.dumps({"message": "how many real observations"}), content_type="application/json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["mode"], "database_only")


class ModelIdentifierTests(TestCase):
    @override_settings(GEMINI_MODEL="gemini-3.8-flash", GEMINI_FALLBACK_MODELS=["gemini-3.6-flash", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite"])
    def test_documented_defaults_are_accepted_as_is(self):
        usable, skipped = ai_assistant.model_candidates()
        self.assertEqual(usable, ["gemini-3.8-flash", "gemini-3.6-flash", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite"])
        self.assertEqual(skipped, [])
        self.assertTrue(set(usable) <= set(ai_assistant.VERIFIED_MODELS))

    def test_project_defaults_use_verified_identifiers(self):
        from django.conf import settings
        self.assertIn(settings.GEMINI_MODEL, ai_assistant.VERIFIED_MODELS)
        self.assertTrue(set(settings.GEMINI_FALLBACK_MODELS) <= set(ai_assistant.VERIFIED_MODELS))
        self.assertNotIn("gemini-3.7-flash", settings.GEMINI_FALLBACK_MODELS)

    @override_settings(GEMINI_MODEL="Gemini 3.8 Flash", GEMINI_FALLBACK_MODELS=["models/gemini-3.6-flash", "gemini-3.7-flash", "gemini-3.8-flash", "gemini-3.6-flash"])
    def test_display_names_deprecated_and_duplicates_are_not_used(self):
        usable, skipped = ai_assistant.model_candidates()
        self.assertEqual(usable, ["gemini-3.6-flash", "gemini-3.8-flash"])   # prefix stripped, 3.7 folded into 3.8, no duplicates
        reasons = " ".join(r for _n, r in skipped)
        self.assertIn("display name", reasons)
        self.assertIn("deprecated", reasons)
        self.assertIn("duplicate", reasons)

    @override_settings(GEMINI_API_KEY="k", GEMINI_MODEL="Gemini 3.8 Flash", GEMINI_FALLBACK_MODELS=[])
    def test_only_display_name_configured_means_no_call_and_graceful_answer(self):
        with mock.patch.object(ai_assistant, "_call_gemini") as call:
            r = ai_assistant.answer_question("hi")
        call.assert_not_called()
        self.assertEqual(r["mode"], "database_only")


class FirmsCommandTests(TestCase):
    """The documented PowerShell command, executed through argparse with a mocked HTTP session."""

    CSV = ("latitude,longitude,brightness,scan,track,acq_date,acq_time,satellite,instrument,confidence,version,bright_t31,frp,daynight\n"
           "34.5,-5.5,330.1,1.1,1.0,2024-06-01,1030,Terra,MODIS,85,6.1,300.2,12.5,D\n")
    AVAIL = "data_id,min_date,max_date\nMODIS_SP,2000-11-01,2025-03-31\nMODIS_NRT,2025-03-27,2026-11-12\nVIIRS_SNPP_SP,2012-01-19,2025-03-31\n"

    def fake_get(self, urls):
        def get(url, timeout=None):
            urls.append(url)
            r = mock.Mock(status_code=200)
            r.text = self.AVAIL if "data_availability" in url else self.CSV
            return r
        return get

    @override_settings(FIRMS_MAP_KEY="KEY123")
    def test_documented_powershell_arguments_work(self):
        urls = []
        with mock.patch("requests.Session.get", side_effect=self.fake_get(urls)):
            out = io.StringIO()
            call_command("import_firms", "--api", "--family", "MODIS", "--bbox", "20.84", "-17.06", "35.74", "-1.54",
                         "--start", "2024-06-01", "--end", "2024-06-07", stdout=out)
        area = [u for u in urls if "/api/area/" in u]
        self.assertEqual(len(area), 2)  # 7 days = 5 + 2 (FIRMS maximum is 5 days per request)
        self.assertTrue(area[0].endswith("/api/area/csv/KEY123/MODIS_SP/-17.06,20.84,-1.54,35.74/5/2024-06-01"), area[0])  # west,south,east,north
        self.assertTrue(area[1].endswith("/MODIS_SP/-17.06,20.84,-1.54,35.74/2/2024-06-06"), area[1])
        job = FetchJob.objects.get()
        self.assertEqual((job.south, job.west, job.north, job.east), (20.84, -17.06, 35.74, -1.54))
        self.assertEqual((job.start_date, job.end_date, job.product), (date(2024, 6, 1), date(2024, 6, 7), "MODIS_SP"))
        self.assertNotIn("KEY123", out.getvalue())
        self.assertEqual(Hotspot.objects.filter(is_demo=False).count(), 1)

    @override_settings(FIRMS_MAP_KEY="KEY123")
    def test_repeated_family_flags_and_sp_nrt_split(self):
        urls = []
        with mock.patch("requests.Session.get", side_effect=self.fake_get(urls)):
            call_command("import_firms", "--api", "--family", "MODIS", "--family", "VIIRS_SNPP", "--bbox", "30", "-10", "36", "-1",
                         "--start", "2025-03-30", "--end", "2025-04-02", stdout=io.StringIO())
        products = sorted({u.split("/")[7] for u in urls if "/api/area/" in u})
        self.assertEqual(products, ["MODIS_NRT", "MODIS_SP", "VIIRS_SNPP_SP"])

    @override_settings(FIRMS_MAP_KEY="KEY123")
    def test_invalid_arguments_are_rejected_before_any_request(self):
        bad = [(["--bbox", "36", "-10", "30", "-1"], "SOUTH"), (["--bbox", "30", "-1", "36", "-10"], "WEST"),
               (["--bbox", "30", "-200", "36", "-1"], "WEST"), (["--bbox", "-95", "-10", "36", "-1"], "SOUTH")]
        with mock.patch("requests.Session.get") as get:
            for args, word in bad:
                with self.assertRaisesMessage(CommandError, word):
                    call_command("import_firms", "--api", "--family", "MODIS", *args, "--start", "2024-06-01", "--end", "2024-06-02")
            with self.assertRaisesMessage(CommandError, "future"):
                call_command("import_firms", "--api", "--family", "MODIS", "--bbox", "30", "-10", "36", "-1", "--start", "2024-06-01",
                             "--end", (date.today() + timedelta(days=3)).isoformat())
            with self.assertRaisesMessage(CommandError, "--start must not be after"):
                call_command("import_firms", "--api", "--family", "MODIS", "--bbox", "30", "-10", "36", "-1", "--start", "2024-06-05", "--end", "2024-06-02")
            with self.assertRaisesMessage(CommandError, "Unknown FIRMS"):
                call_command("import_firms", "--api", "--product", "MODIS_XX", "--bbox", "30", "-10", "36", "-1", "--start", "2024-06-01", "--end", "2024-06-02")
            with self.assertRaises(CommandError):
                call_command("import_firms", "--api", "--family", "MODIS", "--bbox", "30", "-10", "36", "-1", "--start", "06/01/2024", "--end", "2024-06-02")
        get.assert_not_called()

    @override_settings(FIRMS_MAP_KEY="")
    def test_missing_key_message(self):
        with self.assertRaisesMessage(CommandError, "FIRMS_MAP_KEY is not set"):
            call_command("import_firms", "--api", "--family", "MODIS", "--bbox", "30", "-10", "36", "-1", "--start", "2024-06-01", "--end", "2024-06-02")

    def test_purge_demo_keeps_real_rows_and_removes_demo_coverage(self):
        make("MODIS", 34, -5, 1000, "Terra", "MODIS_SP")
        call_command("seed_demo_data", scale=0.02, stdout=io.StringIO())
        self.assertTrue(FetchJob.objects.filter(kind="demo").exists())
        with self.assertRaises(CommandError):  # purge runs first, then the command reports that no files / --api were given
            call_command("import_firms", "--purge-demo", stdout=io.StringIO())
        self.assertFalse(Hotspot.objects.filter(is_demo=True).exists())
        self.assertFalse(FetchJob.objects.filter(kind="demo").exists())
        self.assertEqual(Hotspot.objects.filter(is_demo=False).count(), 1)

    def test_harmonize_command_accepts_parameters(self):
        make("MODIS", 34.5, -5.5, 1030, "Terra", "MODIS_SP"); make("VIIRS", 34.5004, -5.5003, 1100, "N", "VIIRS_SNPP_SP")
        call_command("harmonize_hotspots", "--max-minutes", "20", "--max-radius-km", "3", stdout=io.StringIO())
        from satellite.models import HarmonizationRun
        run = HarmonizationRun.objects.get()
        self.assertEqual((run.parameters["max_minutes"], run.parameters["max_radius_km"], run.stats["overlap_groups"]), (20, 3.0, 0))


class HeuristicLabellingTests(TestCase):
    def test_matching_payload_declares_parameters_as_unvalidated_heuristics(self):
        make("MODIS", 34.5, -5.5, 1030, "Terra", "MODIS_SP", scan=1.0, track=1.0)
        make("VIIRS", 34.5004, -5.5003, 1100, "N", "VIIRS_SNPP_SP", scan=.375, track=.375)
        m = self.client.get(reverse("satellite:harmonization")).json()["matching"]
        ps = m["parameter_status"]
        self.assertFalse(ps["validated"])
        self.assertIn("heuristic", ps["kind"])
        self.assertIn("not universal", ps["statement"])
        self.assertIn("safety cap", ps["radius"])
        self.assertEqual(m["effective_radius_km"], {"min": 0.69, "max": 0.69})   # (1.0 + 0.375) / 2, far below the 6 km cap

    def test_page_labels_parameters(self):
        html = self.client.get(reverse("sat:harmonization")).content.decode()
        self.assertIn("Heuristic parameters", html)
        self.assertIn("not universal scientific thresholds", html)
        self.assertIn("neither value is a validated threshold", html)

    def test_defaults_documented(self):
        self.assertEqual((pipeline.DEFAULT_PARAMS["max_minutes"], pipeline.DEFAULT_PARAMS["max_radius_km"]), (180, 6.0))


class SeparationTests(TestCase):
    """Real and synthetic rows sit at the same places and times; nothing may blend them by default."""

    @classmethod
    def setUpTestData(cls):
        job = FetchJob.objects.create(kind="api", product="MODIS_SP", sensor="MODIS", south=20, west=-18, north=36, east=-1, status="success",
                                      covered_ranges=[["2024-07-01", "2024-07-31"]])
        vjob = FetchJob.objects.create(kind="api", product="VIIRS_SNPP_SP", sensor="VIIRS", south=20, west=-18, north=36, east=-1, status="success",
                                       covered_ranges=[["2024-07-01", "2024-07-31"]])
        djob = FetchJob.objects.create(kind="demo", product="SYNTHETIC", sensor="VIIRS", south=20, west=-18, north=36, east=-1, status="success",
                                       covered_ranges=[["2024-07-01", "2024-07-31"]], rows_invalid=99)
        cls.real_m = make("MODIS", 34.5, -5.5, 1030, "Terra", "MODIS_SP", scan=1.0, track=1.0, frp=10, fetch=job)
        cls.real_v = make("VIIRS", 35.5, -5.5, 1030, "N", "VIIRS_SNPP_SP", fetch=vjob)           # far from the real MODIS
        cls.demo_v = demo("VIIRS", 34.5004, -5.5003, 1031, scan=.4, track=.4)                    # would match the real MODIS if blended
        demo("MODIS", 33.0, -4.0, 1030); demo("MODIS", 33.0, -4.0, 1100, day=D + timedelta(days=1))

    def get(self, name, **q):
        return self.client.get(reverse(f"satellite:{name}"), {"min_conf": "all", **q})

    def test_default_scope_is_real_only_everywhere(self):
        self.assertEqual(self.get("detections").json()["total"], 2)
        self.assertEqual(self.get("detections", format="geojson").json()["total"], 2)
        self.assertEqual(self.get("overview").json()["total"], 2)
        self.assertEqual(self.get("overview").json()["categories"], {"nasa_standard": 2})
        cal = self.get("calendar", start="2024-07-01", end="2024-07-31").json()
        self.assertEqual((cal["summary"]["modis"], cal["summary"]["viirs"], cal["scope"]), (1, 1, "real"))
        self.assertEqual(self.get("day", date="2024-07-11").json()["status"], "zero")      # synthetic row of that day ignored
        self.assertEqual(self.get("temporal").json()["yearly"], [{"year": 2024, "modis": 1, "viirs": 1}])
        self.assertEqual(self.get("spatial").json()["total_cells"], 2)
        self.assertEqual(self.get("clusters", min_pts="2").json()["n_points"], 2)
        rows = b"".join(self.get("export").streaming_content).decode().strip().splitlines()
        self.assertEqual(len(rows), 3)
        self.assertNotIn("synthetic_demo", "\n".join(rows))

    def test_demo_scope_is_demo_only(self):
        self.assertEqual(self.get("detections", scope="demo").json()["total"], 3)
        self.assertEqual(self.get("overview", scope="demo").json()["categories"], {"synthetic_demo": 3})
        self.assertEqual(self.get("calendar", scope="demo", start="2024-07-01", end="2024-07-31").json()["summary"]["viirs"], 1)

    def test_matching_never_pairs_real_with_synthetic(self):
        real = self.get("harmonization").json()
        self.assertEqual((real["matching"]["matched_modis"], real["matching"]["matched_viirs"]), (0, 0))
        demo_ = self.get("harmonization", scope="demo").json()
        self.assertEqual(demo_["matching"]["input_total"], 3)
        self.assertEqual(demo_["matching"]["matched_modis"], 0)   # demo MODIS is not near the demo VIIRS either

    def test_pipeline_flags_stay_inside_each_category(self):
        pipeline.run(D, D + timedelta(days=1), "real")
        pipeline.run(D, D + timedelta(days=1), "demo")
        self.assertEqual(Hotspot.objects.filter(is_demo=False, overlap_group__isnull=False).count(), 0)
        self.assertEqual(Hotspot.objects.filter(is_demo=True, overlap_group__isnull=False).count(), 0)
        with self.assertRaises(ValueError):
            pipeline.run(D, D, "all")

    def test_derived_analyses_refuse_the_combined_scope(self):
        for name in ("summary", "calendar", "harmonization", "clusters", "spatial", "anomalies"):
            r = self.get(name, scope="all", start="2024-07-01", end="2024-07-31")
            self.assertEqual(r.status_code, 400, name)
            self.assertIn("never", r.json()["error"])
        self.assertEqual(self.get("compare", scope="all", start="2024-07-01", end="2024-07-05", compare_start="2024-07-06", compare_end="2024-07-10").status_code, 400)

    def test_combined_scope_is_allowed_only_for_labelled_listings(self):
        d = self.get("detections", scope="all").json()
        self.assertEqual(d["total"], 5)
        cats = {x["category"] for x in d["results"]}
        self.assertEqual(cats, {"nasa_standard", "synthetic_demo"})

    def test_matching_and_clusters_guard_against_mixed_querysets_directly(self):
        from satellite.analysis.overview import matching_summary
        from satellite.analysis import spatial
        mixed = hotspots(scope="all")
        self.assertFalse(matching_summary(mixed)["available"])
        with self.assertRaises(ValueError):
            spatial.clusters(mixed)

    def test_quality_dashboard_counts_categories_separately(self):
        q = self.client.get(reverse("satellite:quality")).json()
        self.assertEqual((q["database"]["real_nasa"]["value"], q["database"]["synthetic_demo"]["value"]), (2, 3))
        self.assertEqual((q["real"]["total"]["value"], q["synthetic"]["total"]["value"]), (2, 3))
        self.assertEqual(q["imports"]["rows_rejected"]["value"], 0)        # the synthetic job's rows_invalid never counts
        self.assertEqual(q["imports"]["count"]["value"], 2)

    def test_ai_uses_real_data_and_labels_synthetic(self):
        r = ai_retrieval.retrieve("What are the latest MODIS and VIIRS observations?")
        self.assertEqual(r["results"]["scope_used_for_details"], "real")
        for rows in r["results"]["latest_observations"].values():
            self.assertTrue(rows and all(o["category"] == "nasa_standard" for o in rows))
        b = r["results"]["base_counts"]
        self.assertEqual((b["real_nasa"]["total"], b["synthetic_demo"]["total"]), (2, 3))
        self.assertIn("NOT NASA", b["synthetic_demo"]["label"])
        text = ai_retrieval.database_only_answer(r)
        self.assertIn("NOT NASA", text)
        with mock.patch.object(ai_assistant, "_call_gemini", return_value=OK) as call, override_settings(GEMINI_API_KEY="k"):
            ai_assistant.answer_question("latest observations")
        sent = call.call_args[0][2].decode()
        self.assertIn("scope_used_for_details", sent)
        self.assertIn("NOT NASA", sent)

    def test_ai_marks_demo_details_as_synthetic_when_no_real_data(self):
        Hotspot.objects.filter(is_demo=False).delete()
        r = ai_retrieval.retrieve("latest observations")
        self.assertEqual(r["results"]["scope_used_for_details"], "demo")
        self.assertTrue(all(o["category"] == "synthetic_demo" for rows in r["results"]["latest_observations"].values() for o in rows))

    def test_ingestion_never_creates_demo_rows_and_ignores_demo_duplicates(self):
        from satellite.services import importer
        job = FetchJob.objects.create(kind="api", product="VIIRS_SNPP_SP", sensor="VIIRS")
        raw = {"latitude": "34.5004", "longitude": "-5.5003", "bright_ti4": "340", "bright_ti5": "290", "scan": ".4", "track": ".4",
               "acq_date": "2024-07-10", "acq_time": "1031", "satellite": "N", "instrument": "VIIRS", "confidence": "n", "version": "2.0", "frp": "3", "daynight": "D"}
        created = importer.store_rows(job, [raw], "VIIRS", "VIIRS_SNPP_SP")
        self.assertEqual(created, 1)                       # identical synthetic row does not count as a duplicate
        self.assertEqual(Hotspot.objects.filter(product="VIIRS_SNPP_SP", is_demo=True).count(), 0)
