from django.conf import settings
from django.db import models


class IncidentReport(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Awaiting verification"
        CONFIRMED = "CONFIRMED", "Confirmed by operator"
        REJECTED = "REJECTED", "False alarm / rejected"
        RESOLVED = "RESOLVED", "Resolved"

    class Source(models.TextChoices):
        IOT = "IOT", "Automatic IoT detection"
        MANUAL = "MANUAL", "Manual operator report"

    station = models.ForeignKey("iot.Station", null=True, blank=True, on_delete=models.SET_NULL, related_name="incidents")
    source = models.CharField(max_length=12, choices=Source.choices)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING, db_index=True)
    title = models.CharField(max_length=160)
    message = models.TextField()
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    evidence = models.JSONField(default=dict, blank=True)
    reporter = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="reviewed_incidents")

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.title} · {self.status}"
