"""Catalogue of NASA FIRMS active-fire products used by POLARIS.

Identifiers come from the official FIRMS Area API page
(https://firms.modaps.eosdis.nasa.gov/api/area). Nothing here is invented:
  MODIS_NRT / MODIS_SP                 MODIS Terra + Aqua (MCD14DL), about 1 km
  VIIRS_SNPP_NRT / VIIRS_SNPP_SP       VIIRS Suomi-NPP, 375 m
  VIIRS_NOAA20_NRT / VIIRS_NOAA20_SP   VIIRS NOAA-20 (JPSS-1), 375 m
  VIIRS_NOAA21_NRT / VIIRS_NOAA21_SP   VIIRS NOAA-21 (JPSS-2), 375 m
SP = Standard Processing (science grade, months of latency), NRT = Near Real-Time
(may include RT/URT, later replaced by SP). LANDSAT_NRT is out of scope.
"""

from dataclasses import dataclass

MODIS, VIIRS = "MODIS", "VIIRS"


@dataclass(frozen=True)
class Product:
    id: str
    sensor: str
    platforms: str
    processing: str  # "SP" or "NRT"
    resolution: str

    @property
    def label(self):
        return f"{self.sensor} {self.platforms} ({self.processing})"


PRODUCTS = {p.id: p for p in [
    Product("MODIS_SP", MODIS, "Terra + Aqua", "SP", "about 1 km"),
    Product("MODIS_NRT", MODIS, "Terra + Aqua", "NRT", "about 1 km"),
    Product("VIIRS_SNPP_SP", VIIRS, "Suomi-NPP", "SP", "375 m"),
    Product("VIIRS_SNPP_NRT", VIIRS, "Suomi-NPP", "NRT", "375 m"),
    Product("VIIRS_NOAA20_SP", VIIRS, "NOAA-20", "SP", "375 m"),
    Product("VIIRS_NOAA20_NRT", VIIRS, "NOAA-20", "NRT", "375 m"),
    Product("VIIRS_NOAA21_SP", VIIRS, "NOAA-21", "SP", "375 m"),
    Product("VIIRS_NOAA21_NRT", VIIRS, "NOAA-21", "NRT", "375 m"),
]}

# Standard-processing product preferred over NRT when both cover a date.
SP_OF_NRT = {pid: pid.replace("_NRT", "_SP") for pid in PRODUCTS if pid.endswith("_NRT")}


def sensor_of(product_id):
    return PRODUCTS[product_id].sensor
