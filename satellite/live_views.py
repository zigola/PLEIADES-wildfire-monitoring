"""JSON endpoints for on-demand NASA FIRMS refresh and the globe's live numbers.

GET  /api/fire/refresh/status/  -> key configured?, cooldown, last sync (also sets the CSRF cookie)
POST /api/fire/refresh/         -> pull today + recent days for an area (or the globe regions)
GET  /api/fire/globe/           -> real last-24h counts per globe region

Set FIRMS_LIVE_PUBLIC=0 to restrict POST /refresh/ to operators (default: anyone, protected by the
per-area cooldown so the FIRMS transaction budget cannot be drained).
"""

from __future__ import annotations

import json

from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST

from accounts.roles import OPERATOR, user_role
from . import globe
from .analysis.params import BadRequest, parse_bbox
from .models import Hotspot
from .services import firms_client as fc, live, tasks

GLOBE_FAMILIES = ("MODIS", "VIIRS_SNPP", "VIIRS_NOAA20")  # keeps the 9-region refresh at ~27 requests
GLOBE_DAYS = 2


def _public():
    return getattr(settings, "FIRMS_LIVE_PUBLIC", True)


def _int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


@ensure_csrf_cookie
@require_GET
def refresh_status(request):
    try:
        bbox = live.norm_bbox(parse_bbox(request.GET))
    except BadRequest as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    last = live.last_sync(bbox)
    start, end = live.window(live.DEFAULT_DAYS)
    return JsonResponse({
        "configured": bool(settings.FIRMS_MAP_KEY), "public": _public(), "cooldown_seconds": live.cooldown_seconds(),
        "retry_after": live.retry_after(bbox), "running": live.is_running(bbox),
        "last_sync_utc": last.isoformat() if last else None, "today_utc": end.isoformat(),
        "window": [start.isoformat(), end.isoformat()],
        "real_rows": Hotspot.objects.filter(is_demo=False).count()})


@require_POST
def refresh(request):
    if not _public() and user_role(request.user) != OPERATOR:
        return JsonResponse({"error": "Live refresh is restricted to operators on this server."}, status=403)
    try:
        body = json.loads(request.body or b"{}")
        if not isinstance(body, dict):
            raise ValueError
    except ValueError:
        return JsonResponse({"error": "Send a JSON object."}, status=400)
    client = fc.FirmsClient()
    if not client.configured:
        return JsonResponse({"status": "no_key", "message": "No FIRMS_MAP_KEY is configured on the server."}, status=503)
    is_operator = user_role(request.user) == OPERATOR
    force = bool(body.get("force")) and is_operator  # only operators may bypass the cooldown

    if body.get("target") == "globe":
        try:
            availability = client.availability("all")
        except fc.FirmsError as exc:
            return JsonResponse({"status": "failed", "message": str(exc)}, status=502)

        def work():
            for region in globe.GLOBE_REGIONS:
                try:
                    live.sync_recent(fc.FirmsClient(), globe.region_bbox(region), GLOBE_DAYS, GLOBE_FAMILIES,
                                     force=force, availability=availability, harmonize=False)
                except (fc.InvalidKey, fc.RateLimited):
                    break
            try:
                from satellite.harmonization import pipeline
                start, end = live.window(GLOBE_DAYS)
                pipeline.run(start, end, "real")
            except Exception:
                pass
        tasks._in_thread(work)
        return JsonResponse({"status": "started", "regions": len(globe.GLOBE_REGIONS)}, status=202)

    try:
        bbox = parse_bbox(body)
    except BadRequest as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    days = max(live.MIN_DAYS, min(live.MAX_DAYS, _int(body.get("days"), live.DEFAULT_DAYS)))
    result = live.sync_recent(client, bbox, days, user=request.user, force=force)
    code = {"failed": 502, "running": 409, "cooldown": 429}.get(result["status"], 200)
    return JsonResponse(result, status=code)


@require_GET
def globe_data(request):
    regions = globe.globe_stats()
    real = Hotspot.objects.filter(is_demo=False).exists()
    return JsonResponse({"has_real_data": real, "window_hours": 24, "regions": regions,
                         "source": "NASA FIRMS (MODIS + VIIRS), last 24 h, UTC",
                         "total": sum(r["count"] for r in regions)})
