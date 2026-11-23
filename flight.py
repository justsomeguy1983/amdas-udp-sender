"""A simulated flight: what AMDAS's three sensors would read, second by second.

One cycle is: on the ground while the GPS gets a fix, climb to about 3,000 ft, cruise (passing through a
cloud, so humidity reaches 100 %), descend, land. Then it starts again, like the unit being switched on
for the next flight.

The values are made up but shaped like the real ones:
  Digiquartz  pressure in psia with 5 decimals, from a standard atmosphere and a slowly drifting QNH
  Rotronic    temperature and humidity with 2 decimals
  Certus      height above the WGS84 ellipsoid (h) and geoid height (N); both 0.0 before a fix, as the
              real Certus reports, so the GPS altitude field is +0000.00 until the fix comes
"""

import math
import random
from dataclasses import dataclass
from decimal import Decimal

from amdas.calc import HPA_PER_PSI

FIELD_ELEVATION_M = 20.0      # the airfield, metres above mean sea level
CRUISE_FT = 3000.0            # above the field
GEOID_N = -32.41              # geoid height seen on the real Certus, metres
NO_FIX_SECONDS = 40           # each cycle starts with no GPS fix ...
FIX_3D_SECONDS = 20           # ... then a 3D fix, then SBAS
FIX_NONE, FIX_3D, FIX_SBAS = 0, 2, 3
FIX_NAMES = {FIX_NONE: "no fix", FIX_3D: "3D fix", FIX_SBAS: "SBAS fix"}

MIN_MINUTES, MAX_MINUTES = 5, 600


@dataclass
class Point:
    """What the sensors read at one second of the flight."""
    phase: str
    elapsed: int                 # seconds into this cycle
    altitude_ft: float           # above the field (the true altitude, before sensor noise)
    pressure: Decimal            # psia, 5 decimals
    temperature: Decimal         # °C, 2 decimals
    humidity: Decimal            # %RH, 2 decimals
    ellipsoid_height: float      # m
    geoid_height: float          # m
    gps_fix: int


def _ease(fraction):
    """0 → 1 with a gentle start and finish."""
    return (1.0 - math.cos(math.pi * min(max(fraction, 0.0), 1.0))) / 2.0


class Flight:
    def __init__(self, minutes=20, seed=None):
        if not MIN_MINUTES <= minutes <= MAX_MINUTES:
            raise ValueError("flight length must be %d to %d minutes" % (MIN_MINUTES, MAX_MINUTES))
        self.total = int(round(minutes * 60))
        ground = max(75, round(0.08 * self.total))
        rest = self.total - 2 * ground
        climb = round(rest * 0.25)
        descent = round(rest * 0.30)
        cruise = rest - climb - descent
        self.segments = [("Ground", ground), ("Climb", climb), ("Cruise", cruise), ("Descent", descent),
                         ("Ground", ground)]
        self.rng = random.Random(seed)

    def _segment(self, t):
        start = 0
        for name, length in self.segments:
            if t < start + length:
                return name, (t - start) / length, start
            start += length
        name, length = self.segments[-1]
        return name, 1.0, self.total - length

    def _altitude_ft(self, phase, fraction, t):
        if phase == "Climb":
            return CRUISE_FT * _ease(fraction)
        if phase == "Cruise":
            return CRUISE_FT + 40.0 * math.sin(2 * math.pi * t / 120.0)
        if phase == "Descent":
            return CRUISE_FT * (1.0 - _ease(fraction))
        return 0.0

    def _cloud(self, phase, fraction):
        """0 outside the cloud, 1 inside it (humidity 100 %), in between at its edges."""
        if phase != "Cruise":
            return 0.0
        if fraction < 0.40 or fraction > 0.60:
            return 0.0
        if fraction < 0.45:
            return _ease((fraction - 0.40) / 0.05)
        if fraction > 0.55:
            return _ease((0.60 - fraction) / 0.05)
        return 1.0

    def at(self, elapsed):
        """The sensor readings `elapsed` seconds after the start (the flight repeats)."""
        t = int(elapsed) % self.total
        phase, fraction, _ = self._segment(t)
        rng = self.rng
        altitude_ft = self._altitude_ft(phase, fraction, t)
        if phase == "Ground" and t < self.total / 2:
            phase = "On the ground, before take-off"
        elif phase == "Ground":
            phase = "Landed"

        msl_m = FIELD_ELEVATION_M + altitude_ft * 0.3048 + rng.gauss(0.0, 0.15)
        qnh_hpa = 1016.0 + 0.4 * math.sin(2 * math.pi * t / self.total)
        hpa = qnh_hpa * (1.0 - 2.25577e-5 * msl_m) ** 5.25588
        psia = hpa / HPA_PER_PSI + rng.gauss(0.0, 0.00002)
        pressure = Decimal("%.5f" % psia)

        temperature = 21.0 - 1.98 * altitude_ft / 1000.0 + rng.gauss(0.0, 0.03)
        cloud = self._cloud(phase, fraction)
        temperature -= 1.5 * cloud
        humidity = 55.0 + 25.0 * altitude_ft / CRUISE_FT
        humidity += (100.0 - humidity) * cloud
        humidity = 100.0 if cloud >= 1.0 else min(humidity + rng.gauss(0.0, 0.15), 99.99)

        if t < NO_FIX_SECONDS:
            fix, h, n = FIX_NONE, 0.0, 0.0
        else:
            fix = FIX_3D if t < NO_FIX_SECONDS + FIX_3D_SECONDS else FIX_SBAS
            n = GEOID_N
            h = FIELD_ELEVATION_M + altitude_ft * 0.3048 + n + rng.gauss(0.0, 0.15)

        return Point(phase=phase, elapsed=t, altitude_ft=altitude_ft, pressure=pressure,
                     temperature=Decimal("%.2f" % temperature), humidity=Decimal("%.2f" % humidity),
                     ellipsoid_height=h, geoid_height=n, gps_fix=fix)
