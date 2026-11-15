# LoRa gateway (separate ESP32)

Gateway responsibilities: receive the field-station payload over LoRa, validate `station_id` and numeric fields, add receiver-side RSSI/SNR and packet sequence/loss information when available, then POST JSON to `https://<django-host>/iot/api/ingest/` with header `X-API-Key: <POLARIS_IOT_API_KEY>`.

Keep gateway hardware separate from the forest node. Use HTTPS in deployment; plain HTTP is suitable only for a trusted local test network. Configure matching LoRa frequency, spreading factor, bandwidth and sync word on both devices. The PLEIADES API accepts telemetry for BME280, PMS5003, MQ-series raw readings, wind, solar/battery and LoRa metrics. Do not fabricate missing values or convert raw gas ADC to ppm without calibration.

This folder documents the gateway contract only. Radio-specific firmware must match the exact SX127x/other LoRa module and pin wiring selected for the build. No radio link or hardware configuration has been tested here.
