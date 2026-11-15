from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [("iot", "0002_lora_solar_metrics")]
    operations = [migrations.AddField("station", "is_simulated", models.BooleanField(default=False))]
