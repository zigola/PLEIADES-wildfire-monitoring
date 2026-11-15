# PLEIADES IoT monitoring and verification

## What's included

- A live IoT command center at `/iot/` using the existing Django base layout.
- Per-station readings and data-presence indicators for BME280, PMS5003, MQ gas sensor, solar telemetry and LoRa link metrics.
- API-key-protected device ingestion, read-only latest telemetry and authenticated station history.
- A human-verification incident workflow: automatic screening reports, operator-created field reports, confirm/reject/resolve actions.
- An IoT vs satellite comparison page at `/iot/comparison/` using geolocated stations and real NASA rows already imported in PLEIADES.
- PLEIADES AI receives a compact current IoT network and incident snapshot in addition to the existing satellite evidence.
- A clearly labelled virtual station simulator. Simulated readings are never presented as physical device telemetry.

## Apply database migrations

From `POLARIS_V2` with the project's virtual environment active:

```powershell
python manage.py migrate
python manage.py check
```

The new migrations are `iot.0002_lora_solar_metrics`, `iot.0003_station_is_simulated`, `iot.0004_environmental_sensor_metrics`, and `alerts.0001_initial`.

## Configure device authentication

Set a long random secret in `.env` (do not commit this file):

```env
POLARIS_IOT_API_KEY=replace-with-a-long-random-secret
```

Restart Django after changing `.env`. The ingestion endpoint rejects requests if the key is missing or invalid.

## Device/gateway API

`POST /iot/api/ingest/`

Headers:

```text
Content-Type: application/json
X-API-Key: <POLARIS_IOT_API_KEY>
```

Example payload (send only fields actually measured by the hardware):

```json
{
  "station_id": "PLEIADES-RIF-01",
  "station_name": "Rif field station",
  "latitude": 34.8,
  "longitude": -4.0,
  "temperature_c": 28.4,
  "humidity_pct": 43.2,
  "pressure_hpa": 1008.4,
  "pm1_ugm3": 4.2,
  "pm25_ugm3": 8.7,
  "pm10_ugm3": 13.1,
  "gas_adc": 820,
  "gas_voltage_mv": 660,
  "smoke_index": 12.0,
  "battery_voltage": 3.91,
  "solar_voltage": 5.3,
  "solar_current_ma": 205.0,
  "solar_power_mw": 1086.5,
  "rssi": -86,
  "lora_snr": 7.5,
  "lora_frequency_mhz": 868.0,
  "lora_spreading_factor": 7,
  "packet_loss_pct": 1.0
}
```

Supported readings: temperature, relative humidity, pressure, PM1.0/PM2.5/PM10, raw gas ADC and voltage, optional device-defined smoke index, wind speed/direction, battery voltage, solar voltage/current/power, LoRa RSSI/SNR/frequency/spreading factor and device-reported packet-loss percentage.

Read-only latest endpoint: `GET /iot/api/latest/`.

Authenticated history endpoint: `GET /iot/api/history/<station_id>/`.

Incident feed: `GET /iot/api/incidents/`. Operators receive every report (with message and evidence). Everyone else receives only confirmed / resolved incidents, without free text or raw evidence, plus `pending_count` (the number of reports awaiting verification), so unverified automatic or field reports are never published.

Satellite comparison JSON: `GET /iot/api/comparison/?station=<station_id>&days=7&radius_km=10`.

## Sensor interpretation

- `DATA RECEIVED` means the latest packet included at least one value for that sensor group. It does not prove wiring, calibration, accuracy or physical health.
- PMS5003 values are sensor-reported particulate mass concentrations in µg/m³.
- MQ-series gas sensor ADC/voltage is a raw response. It is not a calibrated gas concentration (ppm). Do not infer ppm without sensor-specific calibration and environmental compensation.
- Solar power is accepted as a measured/reported field; if computed by firmware, calculate it from voltage and current measured on the same electrical side and state that in firmware documentation.
- LoRa RSSI and SNR are radio-link metrics. The site marks a station ONLINE when any packet arrived in the last 10 minutes; this is not a full end-to-end health check.
- Missing values remain unknown, not zero.

## Fire screening and human verification

The current screening rule is deliberately conservative and explicitly provisional:

- `POTENTIAL_FIRE`: temperature ≥55 °C together with gas ADC ≥2600 or smoke index ≥60; OR temperature ≥40 °C together with both gas ADC ≥2600 and smoke index ≥60.
- `WARNING`: any single high indicator (temperature ≥55 °C, gas ADC ≥2600, or smoke index ≥60).
- `WATCH`: temperature ≥40 °C without the combined conditions above.
- Otherwise `NORMAL`.

These are software demonstration thresholds, not validated fire-detection limits. They must be calibrated using the exact sensor models, analog circuitry, enclosure, placement, weather and field test data before operational use. `POTENTIAL_FIRE` creates a pending report for an operator to verify; it does not confirm a wildfire, notify emergency services, or dispatch a team. Operators can confirm, reject or resolve reports in the IoT page. Manual reports are also operator-only.

## Simulator

Run the server in one terminal, then in another:

```powershell
python manage.py simulate_iot --scenario auto
```

Available scenarios: `normal`, `warning`, `fire`, `auto`; optional `--count 20`, `--interval 3`, `--station PLEIADES-SIM-01`. Simulated stations are visibly labelled `SIMULATED DATA`. This is a software/data-flow simulation only; it does not simulate physical sensors, actual LoRa propagation or a real solar charging circuit.

## Important limits

- IoT ↔ satellite comparison filters real NASA records already stored in the PLEIADES database around the station's GPS location and time period. It does not query NASA live and does not match each packet to a specific satellite detection.
- Satellite hotspots are thermal anomalies, not confirmed fires.
- The manual/automatic incident feed is an internal PLEIADES workflow. SMS, WhatsApp, push notifications, email dispatch to response teams and emergency-service integration are not implemented here.
- Runtime Django tests and hardware/LoRa end-to-end tests must be run in the project's Windows virtual environment and with the actual device firmware. They could not be executed in this file-editing environment because Django dependencies are not installed here.
