from django.db import models


class Station(models.Model):
    station_id = models.CharField(max_length=64, unique=True)
    name = models.CharField(max_length=120, blank=True)
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    is_simulated = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name or self.station_id


class Reading(models.Model):
    station = models.ForeignKey(Station, on_delete=models.CASCADE, related_name="readings")
    temperature_c = models.FloatField(null=True, blank=True)
    humidity_pct = models.FloatField(null=True, blank=True)
    pressure_hpa = models.FloatField(null=True, blank=True)
    pm1_ugm3 = models.FloatField(null=True, blank=True)
    pm25_ugm3 = models.FloatField(null=True, blank=True)
    pm10_ugm3 = models.FloatField(null=True, blank=True)
    gas_adc = models.PositiveIntegerField(null=True, blank=True)
    gas_voltage_mv = models.PositiveIntegerField(null=True, blank=True)
    wind_speed_kmh = models.FloatField(null=True, blank=True)
    wind_direction = models.CharField(max_length=16, blank=True)
    smoke_index = models.FloatField(null=True, blank=True)
    battery_voltage = models.FloatField(null=True, blank=True)
    solar_voltage = models.FloatField(null=True, blank=True)
    solar_current_ma = models.FloatField(null=True, blank=True)
    solar_power_mw = models.FloatField(null=True, blank=True)
    rssi = models.IntegerField(null=True, blank=True)
    lora_snr = models.FloatField(null=True, blank=True)
    lora_frequency_mhz = models.FloatField(null=True, blank=True)
    lora_spreading_factor = models.PositiveSmallIntegerField(null=True, blank=True)
    packet_loss_pct = models.FloatField(null=True, blank=True)
    risk_level = models.CharField(max_length=20, default="NORMAL")
    received_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-received_at"]

    def __str__(self):
        return f"{self.station.station_id} · {self.received_at:%Y-%m-%d %H:%M:%S}"
