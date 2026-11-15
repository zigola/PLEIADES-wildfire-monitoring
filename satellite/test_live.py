"""Live (today) NASA FIRMS refresh: window, cooldown, idempotence, endpoints, globe. No network: sessions are mocked."""

import json
from datetime import date, datetime, timedelta, timezone
from unittest import mock

from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from satellite import globe
from satellite.models import FetchJob, Hotspot
from satellite.services import firms_client as fc, live

def avail(today):
    return ("data_id,min_date,max_date\n"
            "MODIS_SP,2000-11-01,2026-06-30\n" + "".join(
                f"{p},2026-06-01,{today}\n" for p in ("MODIS_NRT", "VIIRS_SNPP_NRT", "VIIRS_NOAA20_NRT", "VIIRS_NOAA21_NRT"))
            + "VIIRS_SNPP_SP,2012-01-19,2026-06-30\n")


M_HEAD = "latitude,longitude,brightness,scan,track,acq_date,acq_time,satellite,instrument,confidence,version,bright_t31,frp,daynight"
V_HEAD = "latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,instrument,confidence,version,bright_ti5,frp,daynight"
TODAY = date(2026, 11, 14)
AOI = (30.0, -10.0, 36.0, -1.0)


class Resp:
    def __init__(self, text, status=200):
        self.text, self.status_code = text, status


def fake_session(today=TODAY):
    """Answers availability and area requests; every area call returns one detection dated `today`."""
    sess = mock.Mock()

    def get(url, timeout=None):
        if "data_availability" in url:
            return Resp(avail(today))
        if "/MODIS_" in url:
            return Resp(M_HEAD + f"\n34.5,-5.5,330.1,1.1,1.0,{today},1030,Terra,MODIS,85,6.1NRT,300.2,12.5,D")
        return Resp(V_HEAD + f"\n34.5003,-5.5002,340.0,0.4,0.4,{today},1031,N,VIIRS,n,2.0NRT,295.0,3.1,D")
    sess.get.side_effect = get
    return sess


def client(sess=None):
    return fc.FirmsClient(map_key="KEY", session=sess or fake_session(), retries=0, sleep=lambda s: None)


class WindowTests(SimpleTestCase):
    def test_window_is_utc_days_and_clamped(self):
        self.assertEqual(live.window(3, TODAY), (date(2026, 11, 12), TODAY))
        self.assertEqual(live.window(0, TODAY), (TODAY, TODAY))
        self.assertEqual(live.window(99, TODAY)[0], TODAY - timedelta(days=9))

    def test_bbox_is_normalised(self):
        self.assertEqual(live.norm_bbox((30, -10.00001, 36, -1)), (30.0, -10.0, 36.0, -1.0))


class SyncTests(TestCase):
    def sync(self, **kw):
        return live.sync_recent(client(kw.pop("sess", None)), AOI, today=TODAY, **kw)

    def test_no_key_does_nothing(self):
        r = live.sync_recent(fc.FirmsClient(map_key=""), AOI, today=TODAY)
        self.assertEqual(r["status"], "no_key")
        self.assertEqual(FetchJob.objects.count(), 0)

    def test_pulls_today_from_nrt_for_every_satellite(self):
        r = self.sync()
        self.assertEqual(r["status"], "ok")
        products = {j["product"] for j in r["jobs"]}
        self.assertTrue({"MODIS_NRT", "VIIRS_SNPP_NRT", "VIIRS_NOAA20_NRT"} <= products, products)
        self.assertFalse(any(p.endswith("_SP") for p in products))  # SP does not reach the last days
        self.assertTrue(Hotspot.objects.filter(acq_date=TODAY, is_demo=False, product__endswith="_NRT").exists())
        self.assertFalse(Hotspot.objects.filter(is_demo=True).exists())
        self.assertTrue(all(j.source_label.startswith(live.LIVE_LABEL) for j in FetchJob.objects.all()))

    def test_cooldown_blocks_repeat_and_force_bypasses(self):
        self.sync()
        again = self.sync()
        self.assertEqual(again["status"], "cooldown")
        self.assertGreater(again["retry_after"], 0)
        self.assertEqual(self.sync(force=True)["status"], "ok")

    @override_settings(FIRMS_LIVE_COOLDOWN_SECONDS=0)
    def test_repeating_is_idempotent(self):
        first = self.sync()
        n = Hotspot.objects.count()
        second = self.sync()
        self.assertGreater(first["rows_created"], 0)
        self.assertEqual(second["rows_created"], 0)
        self.assertEqual(Hotspot.objects.count(), n)

    def test_running_job_blocks_parallel_sync(self):
        FetchJob.objects.create(kind=FetchJob.Kind.API, product="MODIS_NRT", south=30, west=-10, north=36, east=-1,
                                source_label="LIVE sync / MODIS_NRT", status=FetchJob.Status.RUNNING)
        self.assertEqual(self.sync()["status"], "running")

    def test_firms_failure_is_reported_not_raised(self):
        sess = mock.Mock()
        sess.get.side_effect = lambda url, timeout=None: Resp(avail(TODAY)) if "availability" in url else Resp("", 500)
        r = self.sync(sess=sess)
        self.assertEqual(r["status"], "failed")
        self.assertEqual(live.retry_after(AOI), 0)  # a total failure must not start a cooldown
        self.assertEqual(Hotspot.objects.count(), 0)

    def test_iot_tables_untouched(self):
        from iot.models import Station
        before = Station.objects.count()
        self.sync()
        self.assertEqual(Station.objects.count(), before)


class EndpointTests(TestCase):
    def post(self, body, **kw):
        return self.client.post(reverse("satellite:refresh"), json.dumps(body), content_type="application/json", **kw)

    @override_settings(FIRMS_MAP_KEY="")
    def test_no_key_is_503_with_message(self):
        r = self.post({})
        self.assertEqual((r.status_code, r.json()["status"]), (503, "no_key"))

    @override_settings(FIRMS_MAP_KEY="KEY")
    def test_bad_bbox_is_400(self):
        self.assertEqual(self.post({"south": 50, "north": 40}).status_code, 400)

    @override_settings(FIRMS_MAP_KEY="KEY")
    def test_refresh_then_cooldown_429(self):
        with mock.patch("satellite.services.firms_client.requests.Session", return_value=fake_session(live.utc_today())):
            first = self.post({"south": 30, "west": -10, "north": 36, "east": -1, "days": 2})
            second = self.post({"south": 30, "west": -10, "north": 36, "east": -1, "days": 2})
        self.assertEqual(first.status_code, 200, first.content)
        self.assertEqual(first.json()["status"], "ok")
        self.assertEqual(second.status_code, 429)

    @override_settings(FIRMS_MAP_KEY="KEY", FIRMS_LIVE_PUBLIC=False)
    def test_operator_only_mode_blocks_visitors(self):
        self.assertEqual(self.post({}).status_code, 403)

    def test_status_and_globe_are_get_json(self):
        s = self.client.get(reverse("satellite:refresh_status"))
        self.assertEqual(s.status_code, 200)
        self.assertIn("retry_after", s.json())
        g = self.client.get(reverse("satellite:globe")).json()
        self.assertEqual(len(g["regions"]), 9)
        self.assertFalse(g["has_real_data"])
        self.assertEqual(self.client.post(reverse("satellite:globe")).status_code, 405)


class GlobeTests(TestCase):
    def test_counts_only_real_last_24h_rows(self):
        now = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)
        base = dict(sensor="MODIS", latitude=34.5, longitude=-5.0, product="MODIS_NRT", satellite="Terra", frp=10.0)
        Hotspot.objects.create(acq_date=date(2026, 11, 14), acq_time=900, **base)               # inside
        Hotspot.objects.create(acq_date=date(2026, 11, 13), acq_time=1300, **{**base, "latitude": 34.6})  # 23 h ago
        Hotspot.objects.create(acq_date=date(2026, 11, 13), acq_time=1100, **{**base, "latitude": 34.7})  # 25 h ago
        Hotspot.objects.create(acq_date=date(2026, 11, 14), acq_time=800, is_demo=True, **{**base, "latitude": 34.8})
        morocco = next(r for r in globe.globe_stats(now=now) if r["key"] == "morocco")
        self.assertEqual((morocco["count"], morocco["frp_mw"]), (2, 20.0))
