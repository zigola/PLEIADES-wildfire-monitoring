from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("iot", "0001_initial")]
    operations = [
        migrations.AddField("reading", "solar_current_ma", models.FloatField(blank=True, null=True)),
        migrations.AddField("reading", "solar_power_mw", models.FloatField(blank=True, null=True)),
        migrations.AddField("reading", "lora_snr", models.FloatField(blank=True, null=True)),
        migrations.AddField("reading", "lora_frequency_mhz", models.FloatField(blank=True, null=True)),
        migrations.AddField("reading", "lora_spreading_factor", models.PositiveSmallIntegerField(blank=True, null=True)),
        migrations.AddField("reading", "packet_loss_pct", models.FloatField(blank=True, null=True)),
    ]
