from django.contrib import admin

from .models import Hotspot


@admin.register(Hotspot)
class HotspotAdmin(admin.ModelAdmin):
    list_display = ("sensor", "acq_date", "latitude", "longitude", "frp", "confidence", "is_demo")
    list_filter = ("sensor", "confidence", "is_demo", "daynight")
    date_hierarchy = "acq_date"
    show_full_result_count = False
