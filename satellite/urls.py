from django.urls import path

from . import fallback_views, live_views, views

app_name = "satellite"

urlpatterns = [
    path("summary/", views.fire_summary, name="summary"),
    path("cells/", views.fire_cells, name="cells"),
    path("overview/", views.overview, name="overview"),
    path("detections/", views.detections, name="detections"),
    path("day/", views.day_detail, name="day"),
    path("calendar/", views.calendar_view, name="calendar"),
    path("compare/", views.compare_view, name="compare"),
    path("harmonization/", views.harmonization, name="harmonization"),
    path("clusters/", views.clusters, name="clusters"),
    path("spatial/", views.spatial_view, name="spatial"),
    path("temporal/", views.temporal_view, name="temporal"),
    path("anomalies/", views.anomalies_view, name="anomalies"),
    path("quality/", views.quality_view, name="quality"),
    path("imports/", views.imports_view, name="imports"),
    path("export.csv", views.export_csv, name="export"),
    path("refresh/status/", live_views.refresh_status, name="refresh_status"),
    path("refresh/", live_views.refresh, name="refresh"),
    path("globe/", live_views.globe_data, name="globe"),
    path("today/", fallback_views.today_view, name="today"),
    path("sync/status/", fallback_views.sync_status, name="sync_status"),
    path("history/years/", fallback_views.history_years, name="history_years"),
    path("same-date/", fallback_views.same_date, name="same_date"),
]
