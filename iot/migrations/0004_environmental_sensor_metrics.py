from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [("iot", "0003_station_is_simulated")]
    operations = [
        migrations.AddField("reading", "pressure_hpa", models.FloatField(blank=True, null=True)),
        migrations.AddField("reading", "pm1_ugm3", models.FloatField(blank=True, null=True)),
        migrations.AddField("reading", "pm25_ugm3", models.FloatField(blank=True, null=True)),
        migrations.AddField("reading", "pm10_ugm3", models.FloatField(blank=True, null=True)),
    ]
