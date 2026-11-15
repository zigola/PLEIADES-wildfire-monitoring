from django.contrib import admin
from .models import IncidentReport

@admin.register(IncidentReport)
class IncidentReportAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "source", "status", "station", "created_at", "reviewed_by")
    list_filter = ("source", "status", "created_at")
    search_fields = ("title", "message", "station__station_id", "station__name")
    readonly_fields = ("created_at", "reviewed_at", "evidence")
