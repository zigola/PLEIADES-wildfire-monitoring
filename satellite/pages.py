"""HTML pages of the satellite module and its operator-only actions."""

from __future__ import annotations

from django.contrib import messages
from django.core.management import call_command
from django.db.models import Max, Min
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from accounts.decorators import operator_required

from .forms import HarmonizeForm, ImportForm
from .models import FetchJob, Hotspot
from .services import firms_client as fc, tasks
from .services.products import PRODUCTS


def _ctx(active, **extra):
    from django.conf import settings
    return {"sat_active": active, "has_key": bool(settings.FIRMS_MAP_KEY), **extra}


def overview(request):
    return render(request, "satellite/overview.html", _ctx("overview"))


def map_view(request):
    return render(request, "satellite/map.html", _ctx("map"))


def today(request):
    """Today's observations for an area, with historical comparison and clearly labelled statistical estimates."""
    return render(request, "satellite/today.html", _ctx("today"))


def harmonization(request):
    return render(request, "satellite/harmonization.html", _ctx("harmonization"))


def history(request):
    return render(request, "satellite/history.html", _ctx("history"))


def quality(request):
    return render(request, "satellite/quality.html", _ctx("quality"))


def data_management(request):
    initial = {"south": 20.84, "west": -17.06, "north": 35.74, "east": -1.54}
    return render(request, "satellite/data.html", _ctx(
        "data", import_form=ImportForm(initial=initial), harmonize_form=HarmonizeForm(initial={"scope": "real"}),
        products=list(PRODUCTS.values()),
        counts={"real": Hotspot.objects.filter(is_demo=False).count(), "demo": Hotspot.objects.filter(is_demo=True).count()}))


@operator_required
@require_POST
def start_import(request):
    form = ImportForm(request.POST)
    if not form.is_valid():
        for err in sum(form.errors.values(), []) or []:
            messages.error(request, err)
        return redirect("sat:data")
    d = form.cleaned_data
    client = fc.FirmsClient()
    if not client.configured:
        messages.error(request, "No FIRMS_MAP_KEY is configured on the server, so nothing was requested. "
                                "Set FIRMS_MAP_KEY (see .env.example) and restart.")
        return redirect("sat:data")
    bbox = (d["south"], d["west"], d["north"], d["east"])
    started = 0
    for family in d["families"]:
        try:
            jobs = tasks.import_family(client, family, bbox, d["start"], d["end"], request.user, background=True)
            started += len(jobs)
        except (fc.FirmsError, ValueError) as exc:
            messages.error(request, f"{family}: {exc}")
    if started:
        messages.success(request, f"{started} import job(s) started. Progress appears in the import history below.")
    return redirect("sat:data")


@operator_required
@require_POST
def start_harmonization(request):
    form = HarmonizeForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Invalid harmonization request.")
        return redirect("sat:data")
    scope = form.cleaned_data["scope"]
    span = Hotspot.objects.filter(is_demo=(scope == "demo")).aggregate(a=Min("acq_date"), b=Max("acq_date"))
    if span["a"] is None:
        messages.error(request, "There are no rows of this category to harmonize.")
        return redirect("sat:data")
    start, end = form.cleaned_data["start"] or span["a"], form.cleaned_data["end"] or span["b"]
    tasks.harmonize(start, end, scope, background=True)
    messages.success(request, "Harmonization started (duplicate and overlap flagging).")
    return redirect("sat:data")


@operator_required
@require_POST
def load_demo(request):
    """Synthetic data, always labelled as such; replaces only previous synthetic rows."""
    import threading
    from django.db import connection

    def work():
        try:
            call_command("seed_demo_data", scale=0.35)
        finally:
            connection.close()
    threading.Thread(target=work, daemon=True).start()
    messages.success(request, "Generating SYNTHETIC demonstration data (not NASA observations). Refresh in a minute.")
    return redirect("sat:data")


@operator_required
def key_status(request):
    client = fc.FirmsClient()
    if not client.configured:
        return JsonResponse({"configured": False, "message": "No FIRMS_MAP_KEY is set on the server."})
    try:
        st = client.key_status()
    except fc.FirmsError as exc:
        return JsonResponse({"configured": True, "ok": False, "message": str(exc)})
    return JsonResponse({"configured": True, "ok": True, "limit": st.get("transaction_limit"),
                         "used": st.get("current_transactions"), "interval": st.get("transaction_interval")})
