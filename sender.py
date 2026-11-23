"""Sends the AMDAS UDP transmission once a second, built by AMDAS's own code from the simulated flight.

Like AMDAS: one datagram per UTC second, the record followed by CR LF, ASCII, from an unconnected IPv4 UDP
socket (so "port unreachable" replies from the receiver are ignored), broadcast allowed.
"""

import math
import socket
import threading
import time

from amdas.transmission import Sample, build
from flight import Flight


def make_payload(point, utc_second, include_gps_fix=True):
    """The exact bytes AMDAS would send for these sensor readings in this UTC second."""
    sample = Sample(utc_second=utc_second, time_from_pc=False, pressure=point.pressure,
                    temperature=point.temperature, humidity=point.humidity,
                    ellipsoid_height=point.ellipsoid_height, geoid_height=point.geoid_height,
                    gps_fix=point.gps_fix)
    return build(sample).udp_payload(include_gps_fix)


def resolve(host):
    """IPv4 address for a name or address; raises ValueError with a readable message."""
    host = host.strip()
    if not host:
        raise ValueError("enter the receiver's address (127.0.0.1 for this computer)")
    try:
        return socket.getaddrinfo(host, None, socket.AF_INET, socket.SOCK_DGRAM)[0][4][0]
    except (socket.gaierror, UnicodeError):
        raise ValueError("cannot find the address %r" % host)


class Sender:
    """Sends in its own thread. on_sent(payload, point, error, utc_second) is called after every send (error
    is None or the reason the send failed)."""

    def __init__(self, host, port, minutes=20, include_gps_fix=True, on_sent=None, seed=None):
        if not 1 <= int(port) <= 65535:
            raise ValueError("the port must be 1 to 65535")
        self.address = (resolve(host), int(port))
        self.flight = Flight(minutes, seed)
        self.include_gps_fix = include_gps_fix
        self.on_sent = on_sent
        self.sent = 0
        self.errors = 0
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self._stop = threading.Event()
        self._thread = None
        self._start_second = None

    def send_once(self, utc_second):
        """Build and send the transmission for one UTC second. Returns (payload, point, error)."""
        if self._start_second is None:
            self._start_second = utc_second
        point = self.flight.at(utc_second - self._start_second)
        payload = make_payload(point, utc_second, self.include_gps_fix)
        error = None
        try:
            self._socket.sendto(payload, self.address)
            self.sent += 1
        except OSError as exc:
            self.errors += 1
            error = exc.strerror or str(exc)
        if self.on_sent:
            self.on_sent(payload, point, error, utc_second)
        return payload, point, error

    def start(self):
        self._thread = threading.Thread(target=self._run, name="sender", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)
        self._socket.close()

    def _run(self):
        next_second = math.floor(time.time()) + 1
        while not self._stop.is_set():
            wait = next_second - time.time()
            if wait > 2:                              # the PC clock went back: carry on from now
                next_second = math.floor(time.time()) + 1
                continue
            if wait > 0:
                self._stop.wait(min(wait, 0.5))
                continue
            second = int(time.time())
            if second - next_second > 5:          # the PC clock jumped: carry on from now
                next_second = second
            self.send_once(second)
            next_second = second + 1
