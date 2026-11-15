from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
    ]

    operations = [
        migrations.CreateModel(
            name='Hotspot',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('sensor', models.CharField(choices=[('MODIS', 'MODIS (about 1 km)'), ('VIIRS', 'VIIRS (about 375 m)')], max_length=5)),
                ('satellite', models.CharField(blank=True, max_length=10)),
                ('latitude', models.FloatField()),
                ('longitude', models.FloatField()),
                ('acq_date', models.DateField()),
                ('acq_time', models.PositiveSmallIntegerField(default=0, help_text='UTC, HHMM')),
                ('frp', models.FloatField(blank=True, help_text='Fire radiative power, MW', null=True)),
                ('confidence', models.CharField(choices=[('l', 'Low'), ('n', 'Nominal'), ('h', 'High')], default='n', max_length=1)),
                ('daynight', models.CharField(blank=True, max_length=1)),
                ('is_demo', models.BooleanField(default=False, help_text='Synthetic row, not real fire data')),
                ('source', models.CharField(blank=True, max_length=120)),
            ],
            options={
                'ordering': ['acq_date', 'acq_time'],
                'indexes': [
                    models.Index(fields=['sensor', 'acq_date'], name='hotspot_sensor_date_idx'),
                    models.Index(fields=['latitude', 'longitude'], name='hotspot_latlon_idx'),
                ],
            },
        ),
    ]
