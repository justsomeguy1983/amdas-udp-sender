"""The two calculations the logger makes (HARDWARE.md → Calculations)."""

import math

HPA_PER_PSI = 68.9476   # 1 psi in hectopascals (manual factor 68.94757)


def pressure_altitude_ft(psia):
    """Pressure altitude in feet from pressure in psia (NOAA / ICAO standard atmosphere).

    145366.45 × (1 − (P / 1013.25 hPa) ^ 0.190284). Returns None for a pressure that is not positive.
    """
    psia = float(psia)
    if not psia > 0 or not math.isfinite(psia):
        return None
    hpa = psia * HPA_PER_PSI
    return 145366.45 * (1.0 - (hpa / 1013.25) ** 0.190284)


def height_above_sea_level(ellipsoid_height, geoid_height):
    """Height above mean sea level H = h − N (Certus manual §11.4.35).

    h is the Certus height above the WGS84 ellipsoid (packet 20), N the geoid height (packet 54).
    The legacy app computed N − h, which inverted the sign.
    """
    return ellipsoid_height - geoid_height
