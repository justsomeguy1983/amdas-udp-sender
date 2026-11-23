"""Builds the UDP transmission and the CSV row (HARDWARE.md → Output record → Record format).

This file decides exactly what is sent. It has no threads, ports or files: it takes the newest readings
and returns text, so every rule can be tested on its own (tests/test_transmission.py).

The UDP transmission, 7 fields, always 50 characters (CR LF is added when it is sent):

  50932,+14.88424,-0352.59,+0010.65,+22.37,+047.76,3
  SSSSS,±PP.PPPPP,±AAAA.AA,±GGGG.GG,±TT.TT,±HHH.HH,F

  sys_time           seconds since midnight UTC, 5 digits
  pressure           psia, every decimal the Digiquartz sends (5)
  pressure_altitude  feet
  gps_altitude       metres above mean sea level (h − N)
  temperature        °C
  humidity           %RH (3 digits so that 100 % fits)
  gps_fix            Certus fix type 0-7

Every field always has the same width and an explicit sign. A missing or stale value becomes its
placeholder (all zeros, same width), never an empty field. A value too big for its field is held at the
largest value that fits (e.g. +9999.99) and the CSV status says so.

The CSV row is the same 7 fields followed by `utc_datetime` and `status` (CSV only, never sent).
"""

import math
import time
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from . import calc
from .readings import CERTUS, DIGIQUARTZ, GEOID, ROTRONIC

CSV_HEADER = ("sys_time,pressure,pressure_altitude,gps_altitude,temperature,humidity,gps_fix,"
              "utc_datetime,status")

# (digits before the decimal point, digits after) for each number field
PRESSURE = (2, 5)
PRESSURE_ALTITUDE = (4, 2)
GPS_ALTITUDE = (4, 2)
TEMPERATURE = (2, 2)
HUMIDITY = (3, 2)


@dataclass
class StaleLimits:
    """How old (seconds) a reading may be and still be used. Set in config.ini."""
    digiquartz: float = 1.0    # sends ~3 per second
    rotronic: float = 3.0      # sends every 1.2 s
    certus: float = 1.0        # packet 20 at 20 per second
    geoid: float = 60.0        # packet 54; N only changes with horizontal position


@dataclass
class Sample:
    """Everything one UDP transmission needs, taken from the readings at one instant.

    A value is None when its sensor's reading is missing or too old.
    """
    utc_second: int                  # the UTC second this transmission is for (Unix seconds)
    time_from_pc: bool               # True if utc_second came from the Neousys clock, not the Certus
    pressure: object = None          # Decimal psia
    temperature: object = None       # Decimal °C
    humidity: object = None          # Decimal %RH
    ellipsoid_height: object = None  # float m, Certus packet 20
    geoid_height: object = None      # float m, Certus packet 54
    gps_fix: object = None           # int 0-7
    status: list = field(default_factory=list)   # reasons for placeholders and warnings


@dataclass
class Transmission:
    line: str        # the 7 fields, no line ending
    csv_row: str     # line + utc_datetime + status, no line ending
    status: str      # "OK" or reasons joined with "+"

    def udp_payload(self, include_gps_fix=True):
        """Bytes to send: the line plus CR LF. Without gps_fix it is the legacy 6 fields."""
        text = self.line if include_gps_fix else self.line.rsplit(",", 1)[0]
        return (text + "\r\n").encode("ascii")


def _fresh(reading, now, limit):
    if reading is None or now - reading.arrived > limit:
        return None
    return reading.value


def take_sample(readings, now, utc_second, time_from_pc, limits):
    """Pick the values for one transmission.

    readings: dict name → Reading (LatestReadings.snapshot()); now: time.monotonic() of this tick.
    """
    s = Sample(utc_second=utc_second, time_from_pc=time_from_pc)

    pressure = _fresh(readings.get(DIGIQUARTZ), now, limits.digiquartz)
    if pressure is None:
        s.status.append("DIGIQUARTZ_STALE")
    else:
        s.pressure = pressure.value

    rotronic = _fresh(readings.get(ROTRONIC), now, limits.rotronic)
    if rotronic is None:
        s.status.append("ROTRONIC_STALE")
    else:
        s.temperature = rotronic.temperature
        s.humidity = rotronic.humidity
        if rotronic.temperature is None or rotronic.humidity is None:
            s.status.append("ROTRONIC_NO_VALUE")
        if rotronic.simulated:
            s.status.append("ROTRONIC_SIMULATOR")

    state = _fresh(readings.get(CERTUS), now, limits.certus)
    if state is None:
        s.status.append("CERTUS_STALE")
    else:
        s.gps_fix = state.gnss_fix
        s.ellipsoid_height = state.height
        if state.gnss_fix == 0:
            s.status.append("NO_GPS_FIX")
        if state.system_status:
            s.status.append("CERTUS_FAULT")
        geoid = _fresh(readings.get(GEOID), now, limits.geoid)
        if geoid is None:
            s.status.append("NO_GEOID")
        else:
            s.geoid_height = geoid

    if time_from_pc:
        s.status.append("TIME_FROM_PC_CLOCK")
    return s


def fixed(value, int_digits, decimals):
    """Format a number to a fixed width with an explicit sign. Returns (text, over_range).

    fixed(Decimal("9.52757"), 2, 5)  → ("+09.52757", False)
    fixed(-352.5912, 4, 2)           → ("-0352.59", False)
    fixed(11506.8, 4, 2)             → ("+9999.99", True)     held at the largest value that fits
    fixed(None, 3, 2)                → ("+000.00", False)     placeholder
    Rounding is half-up on the exact decimal value; -0.00 is written +0.00.
    """
    step = Decimal(1).scaleb(-decimals)
    width = int_digits + 1 + decimals
    if value is None:
        return "+" + format(Decimal(0), "0%d.%df" % (width, decimals)), False
    if isinstance(value, float):
        if not math.isfinite(value):
            return fixed(None, int_digits, decimals)
        value = Decimal(repr(value))
    value = Decimal(value)
    largest = Decimal(10) ** int_digits - step
    if not value.is_finite():
        return fixed(None, int_digits, decimals)
    # Compare before rounding (rounding a huge, e.g. garbled, number would overflow Decimal's precision).
    # Anything from largest + half a step upwards would round past the largest value that fits.
    limit = largest + step / 2
    if value >= limit or value <= -limit:
        number, over_range = (largest if value > 0 else -largest), True
    else:
        try:
            number = value.quantize(step, rounding=ROUND_HALF_UP)
        except InvalidOperation:
            return fixed(None, int_digits, decimals)
        over_range = False
    sign = "-" if number < 0 else "+"
    return sign + format(abs(number), "0%d.%df" % (width, decimals)), over_range


def build(sample, pressure_decimals=PRESSURE[1]):
    """Turn a Sample into the UDP transmission line and the CSV row.

    pressure_decimals follows [digiquartz] expected_decimals in config.ini (5 for this sensor's settings).
    """
    status = list(sample.status)

    def field_text(value, shape, over_range_name):
        text, over = fixed(value, *shape)
        if over:
            status.append(over_range_name)
        return text

    pressure_altitude = None
    if sample.pressure is not None:
        pressure_altitude = calc.pressure_altitude_ft(sample.pressure)
        if pressure_altitude is None:
            status.append("PRESSURE_ALT_INVALID")
    gps_altitude = None
    if sample.ellipsoid_height is not None and sample.geoid_height is not None:
        gps_altitude = calc.height_above_sea_level(sample.ellipsoid_height, sample.geoid_height)

    fields = [
        "%05d" % (sample.utc_second % 86400),
        field_text(sample.pressure, (PRESSURE[0], pressure_decimals), "PRESSURE_OVER_RANGE"),
        field_text(pressure_altitude, PRESSURE_ALTITUDE, "PRESSURE_ALT_OVER_RANGE"),
        field_text(gps_altitude, GPS_ALTITUDE, "GPS_ALT_OVER_RANGE"),
        field_text(sample.temperature, TEMPERATURE, "TEMPERATURE_OVER_RANGE"),
        field_text(sample.humidity, HUMIDITY, "HUMIDITY_OVER_RANGE"),
        str(sample.gps_fix if sample.gps_fix is not None else 0),
    ]
    line = ",".join(fields)
    status_text = "+".join(status) if status else "OK"
    utc_datetime = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(sample.utc_second))
    return Transmission(line=line, csv_row=line + "," + utc_datetime + "," + status_text,
                        status=status_text)
