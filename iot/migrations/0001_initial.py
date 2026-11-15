from django.db import migrations, models
import django.db.models.deletion

class Migration(migrations.Migration):
    initial = True
    dependencies = []
    operations = [
        migrations.CreateModel(name='Station', fields=[('id',models.BigAutoField(auto_created=True,primary_key=True,serialize=False,verbose_name='ID')),('station_id',models.CharField(max_length=64,unique=True)),('name',models.CharField(blank=True,max_length=120)),('latitude',models.FloatField(blank=True,null=True)),('longitude',models.FloatField(blank=True,null=True)),('is_active',models.BooleanField(default=True)),('created_at',models.DateTimeField(auto_now_add=True))]),
        migrations.CreateModel(name='Reading', fields=[('id',models.BigAutoField(auto_created=True,primary_key=True,serialize=False,verbose_name='ID')),('temperature_c',models.FloatField(blank=True,null=True)),('humidity_pct',models.FloatField(blank=True,null=True)),('gas_adc',models.PositiveIntegerField(blank=True,null=True)),('gas_voltage_mv',models.PositiveIntegerField(blank=True,null=True)),('wind_speed_kmh',models.FloatField(blank=True,null=True)),('wind_direction',models.CharField(blank=True,max_length=16)),('smoke_index',models.FloatField(blank=True,null=True)),('battery_voltage',models.FloatField(blank=True,null=True)),('solar_voltage',models.FloatField(blank=True,null=True)),('rssi',models.IntegerField(blank=True,null=True)),('risk_level',models.CharField(default='NORMAL',max_length=20)),('received_at',models.DateTimeField(auto_now_add=True,db_index=True)),('station',models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,related_name='readings',to='iot.station'))], options={'ordering':['-received_at']}),
    ]
