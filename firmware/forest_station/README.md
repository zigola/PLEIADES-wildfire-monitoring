# Forest station firmware (ESP32)

This is the field-node integration contract. Preserve the original Wokwi project in `POLARIS_V2/iot/wokwi_original/` as the baseline. The field node must sample sensors, calculate a local indicative screening state, and transmit a compact JSON payload over LoRa. Hardware pin mapping and sensor calibration must be confirmed for the actual board before flashing.

Payload contract (include only values actually measured):

```json
{
  "station_id": "PLEIADES-001",
  "station_name": "Rif field station",
  "latitude": 34.8,
  "longitude": -4.0,
  "temperature_c": 34.2,
  "humidity_pct": 28.0,
  "pressure_hpa": 1008.4,
  "pm1_ugm3": 4.2,
  "pm25_ugm3": 8.7,
  "pm10_ugm3": 13.1,
  "gas_adc": 1200,
  "gas_voltage_mv": 970,
  "wind_speed_kmh": 4.2,
  "wind_direction": "NW",
  "smoke_index": 12,
  "battery_voltage": 3.91,
  "solar_voltage": 5.2,
  "solar_current_ma": 205.0,
  "solar_power_mw": 1066.0,
  "rssi": -86,
  "lora_snr": 7.5,
  "lora_frequency_mhz": 868.0,
  "lora_spreading_factor": 7,
  "packet_loss_pct": 1.0
}
```

- BME280 values are temperature, relative humidity and pressure.
- PMS5003 reports particulate mass concentrations; units are µg/m³.
- MQ-series ADC/voltage is a relative raw response, not a calibrated gas concentration or confirmed smoke measurement. Do not label a single gas reading as a confirmed fire.
- Solar power may be calculated as voltage × current only when both are measured on the same side of the circuit; document the measurement point.
- RSSI/SNR must come from the actual LoRa receiver/gateway. Packet loss must be calculated from packet sequence/acknowledgement logic, not guessed.
- The gateway adds/validates radio metadata and forwards the payload to `/iot/api/ingest/` with the configured API key. Use HTTPS outside a trusted local test network.

No production-ready hardware firmware, pin mapping, radio settings or sensor calibration are supplied by this contract alone.
