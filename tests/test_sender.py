"""Checks that the records are AMDAS's, that the flight looks like a flight, and that sending works."""

import os
import socket
import sys
import unittest
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from amdas.transmission import Sample, build                  # noqa: E402
from flight import MAX_MINUTES, MIN_MINUTES, Flight, Point   # noqa: E402
from sender import Sender, make_payload, resolve              # noqa: E402

# 2026-11-21 14:08:52 UTC = 50932 seconds after midnight
T = 1795269600 + 50932 - (1795269600 % 86400)


class SameAsAmdas(unittest.TestCase):
    """The record AMDAS's own tests expect for these readings."""

    def point(self):
        return Point(phase="Cruise", elapsed=0, altitude_ft=0.0, pressure=Decimal("14.88424"),
                     temperature=Decimal("22.37"), humidity=Decimal("47.76"), ellipsoid_height=-21.76,
                     geoid_height=-32.41, gps_fix=3)

    def test_seven_fields(self):
        self.assertEqual(make_payload(self.point(), T),
                         b"50932,+14.88424,-0352.59,+0010.65,+22.37,+047.76,3\r\n")

    def test_legacy_six_fields(self):
        self.assertEqual(make_payload(self.point(), T, include_gps_fix=False),
                         b"50932,+14.88424,-0352.59,+0010.65,+22.37,+047.76\r\n")

    def test_missing_values_are_zero_placeholders(self):
        self.assertEqual(build(Sample(utc_second=T, time_from_pc=True)).line,
                         "50932,+00.00000,+0000.00,+0000.00,+00.00,+000.00,0")


class SimulatedFlight(unittest.TestCase):
    def records(self, minutes=20):
        flight = Flight(minutes, seed=7)
        out = []
        for t in range(flight.total):
            point = flight.at(t)
            out.append((point, make_payload(point, T + t).decode("ascii").rstrip("\r\n")))
        return flight, out

    def test_every_record_has_the_amdas_shape(self):
        _, records = self.records()
        for point, line in records:
            fields = line.split(",")
            self.assertEqual(len(line), 50, line)
            self.assertEqual(len(fields), 7, line)
            self.assertNotIn(" ", line)
            self.assertEqual([len(f) for f in fields], [5, 9, 8, 8, 6, 7, 1], line)

    def test_climbs_to_about_3000_ft_and_lands(self):
        _, records = self.records()
        pressure_altitudes = [float(line.split(",")[2]) for _, line in records]
        self.assertTrue(2900 < max(pressure_altitudes) < 3100, max(pressure_altitudes))
        self.assertLess(abs(pressure_altitudes[0]), 100)
        self.assertLess(abs(pressure_altitudes[-1]), 100)

    def test_gps_fix_comes_and_gps_altitude_follows(self):
        _, records = self.records()
        fixes = [int(line[-1]) for _, line in records]
        self.assertEqual(fixes[0], 0)
        self.assertIn(2, fixes)
        self.assertEqual(fixes[-1], 3)
        before_fix = records[0][1].split(",")[3]
        self.assertEqual(before_fix, "+0000.00")
        top = max(float(line.split(",")[3]) for _, line in records)
        self.assertTrue(900 < top < 960, top)      # 3,000 ft + 20 m field ≈ 934 m

    def test_cloud_takes_humidity_to_100(self):
        _, records = self.records()
        humidity = [line.split(",")[5] for _, line in records]
        self.assertIn("+100.00", humidity)
        self.assertLess(float(humidity[0]), 60)

    def test_repeats(self):
        flight = Flight(5, seed=1)
        self.assertEqual(flight.at(flight.total + 10).elapsed, 10)
        self.assertEqual(flight.at(flight.total + 10).gps_fix, 0)

    def test_every_length_works(self):
        for minutes in (MIN_MINUTES, 7.5, 20, 120, MAX_MINUTES):
            flight = Flight(minutes)
            self.assertEqual(sum(length for _, length in flight.segments), flight.total)
            self.assertTrue(all(length > 0 for _, length in flight.segments), flight.segments)
        for bad in (MIN_MINUTES - 1, MAX_MINUTES + 1):
            with self.assertRaises(ValueError):
                Flight(bad)


class Sending(unittest.TestCase):
    def test_received_bytes_are_the_record(self):
        receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        receiver.bind(("127.0.0.1", 0))
        receiver.settimeout(5)
        sender = Sender("127.0.0.1", receiver.getsockname()[1], seed=3)
        try:
            for second in (T, T + 1):
                payload, point, error = sender.send_once(second)
                self.assertIsNone(error)
                got, source = receiver.recvfrom(1024)
                self.assertEqual(got, payload)
                self.assertEqual(got, make_payload(point, second))
                self.assertEqual(source[0], "127.0.0.1")
            self.assertEqual(sender.sent, 2)
        finally:
            sender.stop()
            receiver.close()

    def test_bad_settings_are_refused(self):
        with self.assertRaises(ValueError):
            resolve("")
        with self.assertRaises(ValueError):
            resolve("no-such-host.invalid")
        with self.assertRaises(ValueError):
            Sender("127.0.0.1", 0)


if __name__ == "__main__":
    unittest.main()
