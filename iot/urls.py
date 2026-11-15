from django.urls import path
from . import views

urlpatterns = [
    path("", views.dashboard, name="iot-dashboard"),
    path("comparison/", views.comparison_page, name="iot-comparison"),
    path("api/ingest/", views.ingest, name="iot-ingest"),
    path("api/latest/", views.latest, name="iot-latest"),
    path("api/history/<str:station_id>/", views.history, name="iot-history"),
    path("api/incidents/", views.incidents, name="iot-incidents"),
    path("api/report-fire/", views.report_fire, name="iot-report-fire"),
    path("api/incidents/<int:incident_id>/review/", views.review_incident, name="iot-review-incident"),
    path("api/comparison/", views.comparison, name="iot-api-comparison"),
]
