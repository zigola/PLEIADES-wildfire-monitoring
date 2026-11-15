# Additive migration: sync log table and two extra indexes on Hotspot. No existing column or row is changed.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('satellite', '0002_provenance_fetchjob_harmonization'),
    ]

    operations = [
        migrations.CreateModel(
            name='SyncRun',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('started_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('finished_at', models.DateTimeField(blank=True, null=True)),
                ('status', models.CharField(choices=[('running', 'Running'), ('ok', 'Completed'), ('partial', 'Completed with errors'), ('failed', 'Failed'), ('rate_limited', 'NASA rate limit reached'), ('no_key', 'Not run: no API key'), ('skipped', 'Skipped (another sync was running)')], default='running', max_length=12)),
                ('trigger', models.CharField(default='scheduler', help_text='scheduler, manual or loop', max_length=12)),
                ('attempt', models.PositiveSmallIntegerField(default=1)),
                ('bbox', models.JSONField(blank=True, default=list, help_text='[south, west, north, east]')),
                ('families', models.JSONField(blank=True, default=list, help_text='Satellite families requested')),
                ('skipped_families', models.JSONField(blank=True, default=dict, help_text='Family -> reason (e.g. satellite no longer delivered by NASA)')),
                ('window_start', models.DateField(blank=True, null=True)),
                ('window_end', models.DateField(blank=True, null=True)),
                ('requests_made', models.PositiveIntegerField(default=0)),
                ('rows_received', models.PositiveIntegerField(default=0)),
                ('rows_created', models.PositiveIntegerField(default=0, help_text='New detections stored by this run')),
                ('newest_observation', models.DateTimeField(blank=True, help_text='Newest acquisition time (UTC) stored for the area after the run', null=True)),
                ('availability', models.JSONField(blank=True, default=dict, help_text='FIRMS data_availability snapshot: product -> [min, max]')),
                ('message', models.TextField(blank=True)),
            ],
            options={
                'ordering': ['-started_at'],
            },
        ),
        migrations.AddIndex(
            model_name='hotspot',
            index=models.Index(fields=['acq_date', 'latitude', 'longitude'], name='hotspot_date_latlon_idx'),
        ),
        migrations.AddIndex(
            model_name='hotspot',
            index=models.Index(fields=['satellite', 'acq_date'], name='hotspot_sat_date_idx'),
        ),
    ]
