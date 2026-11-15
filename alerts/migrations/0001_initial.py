from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True
    dependencies = [("iot", "0001_initial"), migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [migrations.CreateModel(name="IncidentReport", fields=[
        ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
        ("source", models.CharField(choices=[("IOT", "Automatic IoT detection"), ("MANUAL", "Manual operator report")], max_length=12)),
        ("status", models.CharField(choices=[("PENDING", "Awaiting verification"), ("CONFIRMED", "Confirmed by operator"), ("REJECTED", "False alarm / rejected"), ("RESOLVED", "Resolved")], db_index=True, default="PENDING", max_length=12)),
        ("title", models.CharField(max_length=160)), ("message", models.TextField()),
        ("latitude", models.FloatField(blank=True, null=True)), ("longitude", models.FloatField(blank=True, null=True)),
        ("evidence", models.JSONField(blank=True, default=dict)), ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
        ("reviewed_at", models.DateTimeField(blank=True, null=True)),
        ("reporter", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
        ("reviewed_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="reviewed_incidents", to=settings.AUTH_USER_MODEL)),
        ("station", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="incidents", to="iot.station")),
    ], options={"ordering": ["-created_at"]})]
