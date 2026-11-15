import json
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from .models import Station, Reading
from alerts.models import IncidentReport


@override_settings(POLARIS_IOT_API_KEY="test-device-secret")
class IoTIngestionTests(TestCase):
    def post_packet(self, payload):
        return self.client.post(reverse("iot-ingest"), data=json.dumps(payload), content_type="application/json",
                                HTTP_X_API_KEY="test-device-secret")

    def test_ingest_saves_lora_and_solar_telemetry(self):
        response = self.post_packet({"station_id": "TEST-01", "station_name": "Test station", "latitude": 34.8,
            "longitude": -4.0, "temperature_c": 29.5, "humidity_pct": 45, "gas_adc": 800,
            "pressure_hpa": 1008, "pm25_ugm3": 12.5, "pm10_ugm3": 18,
            "solar_voltage": 5.4, "solar_current_ma": 210, "solar_power_mw": 1134,
            "battery_voltage": 3.9, "rssi": -82, "lora_snr": 8.5, "lora_frequency_mhz": 868,
            "lora_spreading_factor": 7, "packet_loss_pct": 1.2})
        self.assertEqual(response.status_code, 201)
        reading = Reading.objects.get()
        self.assertEqual(reading.lora_snr, 8.5)
        self.assertEqual(reading.solar_current_ma, 210)
        self.assertEqual(reading.pm25_ugm3, 12.5)
        self.assertEqual(reading.risk_level, "NORMAL")

    def test_invalid_sensor_range_is_rejected(self):
        response = self.post_packet({"station_id": "TEST-02", "humidity_pct": 180})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Reading.objects.count(), 0)

    def test_multiple_indicators_create_pending_human_review(self):
        response = self.post_packet({"station_id": "TEST-03", "temperature_c": 58, "gas_adc": 3000,
                                     "smoke_index": 75, "latitude": 34.8, "longitude": -4.0})
        self.assertEqual(response.status_code, 201)
        self.assertTrue(response.json()["verification_required"])
        incident = IncidentReport.objects.get()
        self.assertEqual(incident.status, IncidentReport.Status.PENDING)
        self.assertEqual(incident.source, IncidentReport.Source.IOT)

    def test_api_key_is_required(self):
        response = self.client.post(reverse("iot-ingest"), data="{}", content_type="application/json")
        self.assertEqual(response.status_code, 403)


class IoTDashboardTests(TestCase):
    def test_dashboard_and_comparison_pages_render(self):
        station = Station.objects.create(station_id="PAGE-01", name="Page station", latitude=34.8, longitude=-4.0)
        Reading.objects.create(station=station, temperature_c=22, humidity_pct=50)
        self.assertEqual(self.client.get(reverse("iot-dashboard")).status_code, 200)
        self.assertEqual(self.client.get(reverse("iot-comparison")).status_code, 200)
        self.assertEqual(self.client.get(reverse("iot-latest")).status_code, 200)

class IoTRiskAndIncidentWorkflowTests(TestCase):
    def test_risk_requires_multiple_indicators_for_potential_fire(self):
        from .views import _risk
        self.assertEqual(_risk({"temperature_c": 58, "gas_adc": 3000}), "POTENTIAL_FIRE")
        self.assertEqual(_risk({"temperature_c": 58, "gas_adc": 500, "smoke_index": 10}), "WARNING")
        self.assertEqual(_risk({"temperature_c": 42}), "WATCH")
        self.assertEqual(_risk({}), "NORMAL")

    def test_operator_can_submit_and_confirm_manual_report(self):
        User = get_user_model()
        operator = User.objects.create_superuser(username="iot-operator", email="operator@example.test", password="test-pass-123")
        self.client.force_login(operator)
        response = self.client.post(reverse("iot-report-fire"), data=json.dumps({
            "message": "Visible smoke reported near the northern access road.",
            "latitude": 34.8, "longitude": -4.0}), content_type="application/json")
        self.assertEqual(response.status_code, 201)
        incident_id = response.json()["incident_id"]
        review = self.client.post(reverse("iot-review-incident", args=[incident_id]),
            data=json.dumps({"action": "confirm"}), content_type="application/json")
        self.assertEqual(review.status_code, 200)
        self.assertEqual(IncidentReport.objects.get(pk=incident_id).status, IncidentReport.Status.CONFIRMED)


class IoTHardeningTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.operator = User.objects.create_superuser("op", "op@example.test", "test-pass-123")
        cls.member = User.objects.create_user("mem", "mem@example.test", "test-pass-123")
        cls.station = Station.objects.create(station_id="S1", name="Station one", latitude=34.8, longitude=-4.0)
        cls.pending = IncidentReport.objects.create(station=cls.station, source="IOT", title="Potential fire indicator", message="secret field note",
                                                    latitude=34.8, longitude=-4.0, evidence={"gas_adc": 3000})
        cls.confirmed = IncidentReport.objects.create(station=cls.station, source="MANUAL", title="Confirmed", message="operator note",
                                                      status=IncidentReport.Status.CONFIRMED, evidence={"x": 1})

    def feed(self):
        return self.client.get(reverse("iot-incidents")).json()

    def test_public_feed_hides_unverified_reports_message_and_evidence(self):
        d = self.feed()
        self.assertEqual(d["visibility"], "public")
        self.assertEqual([i["status"] for i in d["incidents"]], ["CONFIRMED"])
        self.assertNotIn("message", d["incidents"][0])
        self.assertNotIn("evidence", d["incidents"][0])
        self.assertEqual(d["pending_count"], 1)                       # the number is public, the content is not
        self.assertNotIn("secret field note", json.dumps(d))

    def test_member_is_not_an_operator(self):
        self.client.force_login(self.member)
        d = self.feed()
        self.assertEqual((d["visibility"], len(d["incidents"])), ("public", 1))

    def test_operator_sees_everything(self):
        self.client.force_login(self.operator)
        d = self.feed()
        self.assertEqual(d["visibility"], "operator")
        self.assertEqual(len(d["incidents"]), 2)
        pending = next(i for i in d["incidents"] if i["status"] == "PENDING")
        self.assertEqual((pending["message"], pending["evidence"]), ("secret field note", {"gas_adc": 3000}))

    def test_latest_uses_a_constant_number_of_queries(self):
        for n in range(8):
            st = Station.objects.create(station_id=f"Q{n}", name=f"Q{n}", latitude=30 + n, longitude=-5)
            Reading.objects.create(station=st, temperature_c=20 + n)
            Reading.objects.create(station=st, temperature_c=30 + n)
        with self.assertNumQueries(2):
            d = self.client.get(reverse("iot-latest")).json()
        newest = {s["station_id"]: s["latest"]["temperature_c"] for s in d["stations"] if s["latest"]}
        self.assertEqual(newest["Q3"], 33)                            # the newest reading, not an arbitrary one
        self.assertIsNone(next(s for s in d["stations"] if s["station_id"] == "S1")["latest"])  # no reading = unknown

    @override_settings(POLARIS_IOT_API_KEY="right-key")
    def test_api_key_comparison_handles_wrong_length_and_unicode(self):
        url = reverse("iot-ingest")
        for key in ("", "right-ke", "right-key-and-more", "clé-incorrecte"):
            r = self.client.post(url, data='{"station_id": "X"}', content_type="application/json", HTTP_X_API_KEY=key)
            self.assertEqual(r.status_code, 403, key)
        ok = self.client.post(url, data='{"station_id": "X"}', content_type="application/json", HTTP_X_API_KEY="right-key")
        self.assertEqual(ok.status_code, 201)

    @override_settings(POLARIS_IOT_API_KEY="")
    def test_no_configured_key_rejects_everything(self):
        r = self.client.post(reverse("iot-ingest"), data='{"station_id": "X"}', content_type="application/json", HTTP_X_API_KEY="")
        self.assertEqual(r.status_code, 403)

    def test_duplicate_dashboard_templates_stay_identical(self):
        from pathlib import Path
        base = Path(__file__).resolve().parent.parent
        for name in ("dashboard.html", "comparison.html"):
            self.assertEqual((base / "templates" / "iot" / name).read_bytes(), (base / "iot" / "templates" / "iot" / name).read_bytes(),
                             f"{name} differs between templates/iot and iot/templates/iot; edit both or remove one")
