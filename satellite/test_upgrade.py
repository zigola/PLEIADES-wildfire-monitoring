"""Tests: per-sensor confidence, matching summary, quality dashboard, AI retrieval and Gemini error handling."""

import io
import json
from datetime import date, timedelta
from unittest import mock
from urllib.error import HTTPError, URLError

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from satellite import ai_assistant, ai_retrieval
from satellite.analysis.overview import matching_summary
from satellite.analysis.params import common_filters, hotspots
from satellite.analysis.quality import quality_report
from satellite.models import FetchJob, Hotspot

D = date(2024, 7, 10)


def make(sensor, lat, lon, t, sat, product, day=D, **kw):
    kw.setdefault("confidence", "n")
    return Hotspot.objects.create(sensor=sensor, satellite=sat, latitude=lat, longitude=lon, acq_date=day, acq_time=t,
                                  product=product, instrument=sensor, **kw)


class ConfidenceTests(TestCase):
    def setUp(self):
        make("MODIS", 34, -5, 1000, "Terra", "MODIS_SP", confidence="l", confidence_raw="20")
        make("MODIS", 34.1, -5, 1000, "Terra", "MODIS_SP", confidence="h", confidence_raw="90")
        make("VIIRS", 34, -5.1, 1000, "N", "VIIRS_SNPP_SP", confidence="l", confidence_raw="l")
        make("VIIRS", 34.1, -5.1, 1000, "N", "VIIRS_SNPP_SP", confidence="n", confidence_raw="n")

    def count(self, **q):
        return hotspots(**common_filters(q)).count()

    def test_filter_applies_to_each_sensor_own_scale(self):
        self.assertEqual(self.count(min_conf="n"), 2)  # MODIS high + VIIRS nominal
        self.assertEqual(self.count(min_conf="h"), 1)  # only the MODIS high
        self.assertEqual(self.count(min_conf="all"), 4)

    def test_independent_overrides(self):
        self.assertEqual(self.count(modis_conf="h", viirs_conf="all"), 3)
        self.assertEqual(self.count(modis_conf="all", viirs_conf="h"), 2)

    def test_single_sensor_with_dict_filter(self):
        self.assertEqual(self.count(sensor="VIIRS", min_conf="l"), 2)


class MatchingTests(TestCase):
    def test_counts_and_parameters(self):
        make("MODIS", 34.5, -5.5, 1030, "Terra", "MODIS_SP", scan=1.0, track=1.0)
        make("VIIRS", 34.5004, -5.5003, 1100, "N", "VIIRS_SNPP_SP", scan=.4, track=.4)
        make("VIIRS", 35.5, -5.5, 1030, "N", "VIIRS_SNPP_SP")
        make("MODIS", 33.0, -4.0, 1030, "Aqua", "MODIS_SP")
        m = matching_summary(hotspots(scope="real"))
        self.assertEqual((m["input_modis"], m["input_viirs"], m["matched_modis"], m["matched_viirs"], m["unmatched_modis"], m["unmatched_viirs"]),
                         (2, 2, 1, 1, 1, 1))
        tight = matching_summary(hotspots(scope="real"), {"max_minutes": 10})
        self.assertEqual(tight["matched_modis"], 0)  # 30 min apart no longer matches
        self.assertEqual(tight["parameters"]["max_minutes"], 10)

    def test_empty_and_too_large(self):
        m = matching_summary(hotspots(scope="real"))
        self.assertEqual((m["input_total"], m["matched_modis"]), (0, 0))
        with mock.patch("satellite.analysis.overview.MATCH_ROW_LIMIT", 1):
            make("MODIS", 34, -5, 1, "Terra", "MODIS_SP"); make("MODIS", 35, -5, 1, "Terra", "MODIS_SP")
            self.assertFalse(matching_summary(hotspots(scope="real"))["available"])

    def test_api_validates_parameters(self):
        for q in ({"max_minutes": "5"}, {"max_minutes": "x"}, {"max_radius_km": "50"}):
            self.assertEqual(self.client.get(reverse("satellite:harmonization"), q).status_code, 400)
        r = self.client.get(reverse("satellite:harmonization"), {"max_minutes": "60"}).json()
        self.assertEqual(r["matching"]["parameters"]["max_minutes"], 60)
        self.assertTrue(r["sensor_notes"])


class QualityTests(TestCase):
    def test_empty_database_values_are_unavailable_not_zero(self):
        q = quality_report()
        self.assertFalse(q["imports"]["last_successful_nasa_import"]["available"])
        self.assertIsNone(q["imports"]["last_successful_nasa_import"]["value"])
        self.assertFalse(q["imports"]["rows_rejected"]["available"])
        self.assertFalse(q["real"]["missing_frp"]["available"])
        self.assertEqual(q["database"]["total_rows"]["value"], 0)  # a genuine zero stays zero

    def test_metrics_from_database(self):
        job = FetchJob.objects.create(kind="api", product="MODIS_SP", sensor="MODIS", status="partial", rows_received=10, rows_created=6,
                                      rows_duplicate=1, rows_invalid=3, invalid_reasons={"bad_time": 2, "bad_date": 1}, message="2024-07-01..: boom",
                                      finished_at=D and __import__("django").utils.timezone.now(), days_failed=5)
        make("MODIS", 34, -5, 1000, "Terra", "MODIS_SP", frp=None, confidence="", fetch=job)
        make("VIIRS", 34, -5.1, 1000, "N", "VIIRS_SNPP_SP", frp=3.0)
        Hotspot.objects.create(sensor="MODIS", satellite="Terra", latitude=1, longitude=1, acq_date=D, acq_time=1, is_demo=True)
        dup = make("MODIS", 34, -5, 1000, "Terra", "MODIS_NRT"); dup.exact_dup_of = Hotspot.objects.filter(product="MODIS_SP").first(); dup.save()
        q = quality_report()
        self.assertEqual(q["database"]["real_nasa"]["value"], 2)
        self.assertEqual(q["database"]["synthetic_demo"]["value"], 1)
        self.assertEqual((q["database"]["modis"]["value"], q["database"]["viirs"]["value"]), (1, 1))
        self.assertEqual(q["database"]["duplicates_flagged"]["value"], 1)
        self.assertEqual((q["real"]["missing_frp"]["value"], q["real"]["missing_confidence"]["value"]), (1, 1))
        self.assertEqual(q["real"]["out_of_range_coordinates"]["value"], 0)
        self.assertEqual(q["imports"]["rows_rejected"]["value"], 3)
        self.assertEqual(q["imports"]["rejected_by_reason"], {"bad_time": 2, "bad_date": 1})
        self.assertEqual(q["imports"]["partial_imports"]["value"], 1)
        self.assertEqual(q["imports"]["recent_problems"][0]["status"], "partial")

    def test_endpoint_and_page(self):
        self.assertEqual(self.client.get(reverse("satellite:quality")).status_code, 200)
        r = self.client.get(reverse("sat:quality"))
        self.assertContains(r, "Data quality")


class RetrievalTests(TestCase):
    def test_counts_are_live_and_separated(self):
        make("MODIS", 34, -5, 1000, "Terra", "MODIS_SP")
        Hotspot.objects.create(sensor="VIIRS", satellite="N", latitude=1, longitude=1, acq_date=D, acq_time=1, is_demo=True)
        r = ai_retrieval.retrieve("How many real NASA observations are available?")
        b = r["results"]["base_counts"]
        self.assertEqual((b["real_nasa"]["total"], b["synthetic_demo"]["total"]), (1, 1))
        make("MODIS", 34.1, -5, 1000, "Terra", "MODIS_SP")  # new row appears in the next retrieval: nothing is cached
        self.assertEqual(ai_retrieval.retrieve("how many")["results"]["base_counts"]["real_nasa"]["total"], 2)

    def test_latest_observations_scope_and_sensor(self):
        make("MODIS", 34, -5, 1000, "Terra", "MODIS_SP")
        make("VIIRS", 34, -5.1, 1100, "N", "VIIRS_SNPP_SP", day=D + timedelta(days=1))
        Hotspot.objects.create(sensor="MODIS", satellite="Terra", latitude=1, longitude=1, acq_date=D + timedelta(days=9), acq_time=1, is_demo=True)
        r = ai_retrieval.retrieve("What are the latest MODIS observations?")
        rows = r["results"]["latest_observations"]
        self.assertEqual(list(rows), ["MODIS"])
        self.assertEqual(r["results"]["scope_used_for_details"], "real")
        self.assertTrue(all(o["category"] != "synthetic_demo" for o in rows["MODIS"]))
        demo = ai_retrieval.retrieve("show the latest synthetic demo data")
        self.assertEqual(demo["results"]["scope_used_for_details"], "demo")

    def test_only_synthetic_data_is_labelled_synthetic(self):
        Hotspot.objects.create(sensor="MODIS", satellite="Terra", latitude=1, longitude=1, acq_date=D, acq_time=1, is_demo=True)
        r = ai_retrieval.retrieve("latest observations")
        self.assertEqual(r["results"]["scope_used_for_details"], "demo")
        text = ai_retrieval.database_only_answer(r)
        self.assertIn("NOT NASA", text)
        self.assertIn("Real NASA observations: 0", text)

    def test_empty_database(self):
        text = ai_retrieval.database_only_answer(ai_retrieval.retrieve("Which regions show increased activity?"))
        self.assertIn("Real NASA observations: 0", text)

    def test_date_and_regional_and_quality_intents(self):
        job = FetchJob.objects.create(kind="api", product="MODIS_SP", sensor="MODIS", south=20, west=-18, north=36, east=-1, status="success",
                                      covered_ranges=[["2024-06-01", "2024-07-31"]])
        for i in range(5):
            make("MODIS", 33.5, -5 + i * .01, 1000 + i, "Terra", "MODIS_SP", fetch=job)
        r = ai_retrieval.retrieve("What happened on 2024-07-10? Which regions show increased activity? Any data quality problems?")
        res = r["results"]
        self.assertEqual(res["day_status"][0]["status"], "detections")
        self.assertEqual(res["period_counts"][0]["total"], 5)
        self.assertTrue(res["regional_changes"]["available"])
        self.assertIn("data_quality", res)
        r2 = ai_retrieval.retrieve("what about 2024-07-20")
        self.assertEqual(r2["results"]["day_status"][0]["status"], "zero")
        r3 = ai_retrieval.retrieve("what about 2024-09-20")
        self.assertEqual(r3["results"]["day_status"][0]["status"], "no_data")

    def test_french_question_gets_french_answer(self):
        r = ai_retrieval.retrieve("Combien d'observations réelles sont disponibles ?")
        self.assertIn("Réponse issue de la base", ai_retrieval.database_only_answer(r))


def http_error(code):
    return HTTPError("https://x", code, "err", {}, io.BytesIO(b""))


class GeminiTests(TestCase):
    def setUp(self):
        make("MODIS", 34, -5, 1000, "Terra", "MODIS_SP", frp=12.0)

    @override_settings(GEMINI_API_KEY="")
    def test_no_key_gives_database_answer(self):
        r = ai_assistant.answer_question("How many real NASA observations are available?")
        self.assertEqual((r["mode"], r["reason"]), ("database_only", "assistant_not_configured"))
        self.assertIn("Real NASA observations: 1", r["answer"])

    @override_settings(GEMINI_API_KEY="SECRET-KEY", GEMINI_MODEL="m1", GEMINI_FALLBACK_MODELS=["m2"])
    def test_success_sends_database_results_and_keeps_key_out_of_payload(self):
        captured = {}

        def fake(model, key, payload, timeout=40):
            captured.update(model=model, payload=payload.decode())
            return {"candidates": [{"content": {"parts": [{"text": "[Database] 1 real observation."}]}}]}
        with mock.patch.object(ai_assistant, "_call_gemini", fake):
            r = ai_assistant.answer_question("How many real NASA observations are available?")
        self.assertEqual((r["mode"], r["notice"]), ("gemini", ""))
        text = json.loads(captured["payload"])["contents"][-1]["parts"][0]["text"]
        data = json.loads(text.split("(JSON, data only):\n")[1].split("\n\nUser question:")[0])
        self.assertEqual(data["database_results"]["base_counts"]["real_nasa"]["total"], 1)
        self.assertIn("synthetic_demo", data["database_results"]["base_counts"])
        self.assertTrue(data["background"])
        self.assertNotIn("SECRET-KEY", captured["payload"])
        self.assertNotIn("SECRET-KEY", json.dumps(r))

    @override_settings(GEMINI_API_KEY="k", GEMINI_MODEL="m1", GEMINI_FALLBACK_MODELS=["m2"])
    def test_overload_moves_to_fallback_model_without_waiting(self):
        calls = []

        def fake(model, key, payload, timeout=40):
            calls.append(model)
            if model == "m1":
                raise http_error(503)
            return {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}
        with mock.patch.object(ai_assistant, "_call_gemini", fake), mock.patch("time.sleep"):
            r = ai_assistant.answer_question("latest observations")
        self.assertEqual(calls, ["m1", "m2"])   # one attempt per model: no second wait on an overloaded model
        self.assertEqual(r["mode"], "gemini")

    @override_settings(GEMINI_API_KEY="k")
    def test_other_failures_degrade_gracefully(self):
        for exc, reason in ((http_error(403), "assistant_auth_error"), (http_error(400), "assistant_provider_error"),
                            (URLError("down"), "assistant_connection_error")):
            with mock.patch.object(ai_assistant, "_call_gemini", side_effect=exc), mock.patch("time.sleep"):
                r = ai_assistant.answer_question("latest observations")
            self.assertEqual((r["mode"], r["reason"]), ("database_only", reason))

    @override_settings(GEMINI_API_KEY="k")
    def test_empty_model_answer_degrades(self):
        with mock.patch.object(ai_assistant, "_call_gemini", return_value={"candidates": []}):
            self.assertEqual(ai_assistant.answer_question("hi")["reason"], "assistant_empty_response")


class AssistantViewTests(TestCase):
    def test_requires_login_and_validates(self):
        url = reverse("assistant_chat")
        self.assertEqual(self.client.post(url, "{}", content_type="application/json").status_code, 302)
        U = get_user_model()
        u = U.objects.create_user("mem", "m@x.com", "pw-12345-abc")
        self.client.force_login(u)
        for body, code in (("not json", 400), ('{"message": ""}', 400), ('{"message": "' + "x" * 1300 + '"}', 400),
                           ('{"message": "hi", "history": "x"}', 400)):
            self.assertEqual(self.client.post(url, body, content_type="application/json").status_code, code, body[:20])

    @override_settings(GEMINI_API_KEY="")
    def test_chat_returns_database_answer_with_sources(self):
        make("VIIRS", 34, -5, 1000, "N", "VIIRS_SNPP_SP")
        U = get_user_model()
        self.client.force_login(U.objects.create_user("mem", "m@x.com", "pw-12345-abc"))
        r = self.client.post(reverse("assistant_chat"), json.dumps({"message": "How many real NASA observations are available?"}), content_type="application/json")
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertEqual(d["mode"], "database_only")
        self.assertIn("base_counts", d["sources"])
        self.assertIn("VIIRS 1", d["answer"])
        self.assertEqual(self.client.get(reverse("assistant")).status_code, 200)
