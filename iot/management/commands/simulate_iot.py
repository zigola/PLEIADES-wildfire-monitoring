"""Generate clearly labelled virtual POLARIS forest-station telemetry."""
import math, random, time
from django.core.management.base import BaseCommand
from django.utils import timezone
from iot.models import Station, Reading
from iot.views import _risk, _register_auto_incident

class Command(BaseCommand):
    help = "Run a virtual IoT forest station (no physical hardware required)."
    def add_arguments(self, parser):
        parser.add_argument('--interval', type=float, default=3.0, help='Seconds between readings')
        parser.add_argument('--count', type=int, default=0, help='Number of readings; 0 runs until Ctrl+C')
        parser.add_argument('--station', default='POLARIS-SIM-01')
        parser.add_argument('--name', default='Virtual Forest Station — Rif')
        parser.add_argument('--lat', type=float, default=34.8)
        parser.add_argument('--lon', type=float, default=-4.0)
        parser.add_argument('--scenario', choices=['auto','normal','warning','fire'], default='auto')
    def handle(self, *args, **o):
        station, _ = Station.objects.get_or_create(station_id=o['station'], defaults={'name':o['name'],'latitude':o['lat'],'longitude':o['lon']})
        station.name=o['name']; station.latitude=o['lat']; station.longitude=o['lon']; station.is_active=True; station.is_simulated=True; station.save()
        n=0
        self.stdout.write(self.style.SUCCESS('Virtual station started. No hardware, sensors, LoRa or internet device required. Ctrl+C to stop.'))
        try:
            while not o['count'] or n < o['count']:
                phase = (n // 10) % 3
                mode = o['scenario'] if o['scenario'] != 'auto' else ['normal','warning','fire'][phase]
                t=27+2*math.sin(n/5)+random.uniform(-.5,.5); h=48+4*math.sin(n/7)+random.uniform(-2,2); gas=900+random.randint(-80,100); smoke=12+random.random()*8
                if mode=='warning': t+=30; h-=18; gas+=1150; smoke+=35
                if mode=='fire': t+=27; h-=28; gas+=2200; smoke+=70
                wind=8+random.random()*12
                solar_v=round(5.2+random.random()*1.5,2); solar_i=round(180+random.random()*120,1)
                values={'temperature_c':round(t,1),'humidity_pct':round(max(0,min(100,h)),1),'pressure_hpa':round(1005+random.uniform(-4,4),1),
                    'pm1_ugm3':round(4+random.random()*8,1),'pm25_ugm3':round(7+random.random()*15,1),'pm10_ugm3':round(10+random.random()*22,1),'gas_adc':max(0,int(gas)),
                    'gas_voltage_mv':int(gas/4095*3300),'wind_speed_kmh':round(wind,1),
                    'wind_direction':random.choice(['N','NE','E','SE','S','SW','W','NW']),
                    'smoke_index':round(smoke,1),'battery_voltage':round(3.7+random.random()*.25,2),
                    'solar_voltage':solar_v,'solar_current_ma':solar_i,'solar_power_mw':round(solar_v*solar_i,1),
                    'rssi':-65-random.randint(0,20),'lora_snr':round(5+random.random()*8,1),
                    'lora_frequency_mhz':868.0,'lora_spreading_factor':7,'packet_loss_pct':round(random.random()*2,2)}
                risk=_risk(values)
                r=Reading.objects.create(station=station,risk_level=risk,**values)
                _register_auto_incident(station,r)
                self.stdout.write(f"{timezone.localtime(r.received_at):%H:%M:%S} | {mode.upper():7} | {risk:14} | T={t:.1f}C H={h:.1f}% gas={gas} smoke={smoke:.1f}")
                n+=1
                if o['count'] and n>=o['count']: break
                time.sleep(max(.2,o['interval']))
        except KeyboardInterrupt: self.stdout.write(self.style.WARNING('Simulation stopped.'))
