from django.urls import path

from . import pages

app_name = "sat"

urlpatterns = [
    path("", pages.overview, name="overview"),
    path("map/", pages.map_view, name="map"),
    path("today/", pages.today, name="today"),
    path("harmonization/", pages.harmonization, name="harmonization"),
    path("history/", pages.history, name="history"),
    path("quality/", pages.quality, name="quality"),
    path("data/", pages.data_management, name="data"),
    path("data/import/", pages.start_import, name="start_import"),
    path("data/harmonize/", pages.start_harmonization, name="start_harmonize"),
    path("data/demo/", pages.load_demo, name="load_demo"),
    path("data/key-status/", pages.key_status, name="key_status"),
]
