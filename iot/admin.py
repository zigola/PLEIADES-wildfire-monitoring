from django.contrib import admin
from .models import Station, Reading

@admin.register(Station)
class StationAdmin(admin.ModelAdmin):
    list_display = ("station_id", "name", "is_active", "is_simulated", "latitude", "longitude", "created_at")
    list_filter = ("is_active", "is_simulated")
    search_fields = ("station_id", "name")

@admin.register(Reading)
class ReadingAdmin(admin.ModelAdmin):
    list_display = ("station", "received_at", "risk_level", "temperature_c", "gas_adc", "solar_voltage", "battery_voltage", "rssi")
    list_filter = ("risk_level", "received_at")
    date_hierarchy = "received_at"
