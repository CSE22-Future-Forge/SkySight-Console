"""NMEA 0183 pose reader: GGA gives position/altitude, RMC/VTG give speed and course."""

from __future__ import annotations

import logging
import socket
import threading
import time

import pynmea2

from ground_station.core.models import TelemetryIn
from ground_station.core.services import ServiceError

log = logging.getLogger(__name__)

KNOTS_TO_MPS = 0.514444


class NmeaReader(threading.Thread):
    """``target`` is "udp:<port>" or "serial:<port>:<baud>"."""

    def __init__(self, manifest, target: str = "udp:10110"):
        super().__init__(name="nmea-reader", daemon=True)
        self.manifest = manifest
        self.target = target
        self.state: dict = {}
        self.sentences = 0
        self.last_error: str | None = None
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def handle(self, line: str) -> TelemetryIn | None:
        try:
            msg = pynmea2.parse(line.strip())
        except pynmea2.ParseError:
            return None
        s = self.state
        kind = msg.sentence_type
        if kind == "RMC" and msg.status == "A":
            if msg.spd_over_grnd is not None:
                s["ground_speed_mps"] = float(msg.spd_over_grnd) * KNOTS_TO_MPS
            if msg.true_course is not None:
                s["heading_deg"] = float(msg.true_course)
        elif kind == "VTG":
            if msg.true_track is not None:
                s["heading_deg"] = float(msg.true_track)
            if msg.spd_over_grnd_kmph is not None:
                s["ground_speed_mps"] = float(msg.spd_over_grnd_kmph) / 3.6
        elif kind == "GGA" and msg.gps_qual and int(msg.gps_qual) > 0:
            return TelemetryIn(ts=int(time.time() * 1000), lat=msg.latitude, lon=msg.longitude,
                               alt_msl_m=float(msg.altitude) if msg.altitude is not None else None,
                               heading_deg=s.get("heading_deg"), ground_speed_mps=s.get("ground_speed_mps"))
        return None

    def _lines(self):
        kind, _, rest = self.target.partition(":")
        if kind == "udp":
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.bind(("0.0.0.0", int(rest)))
                sock.settimeout(1)
                while not self._stop.is_set():
                    try:
                        data = sock.recv(4096)
                    except socket.timeout:
                        continue
                    yield from data.decode("ascii", "ignore").splitlines()
        elif kind == "serial":
            import serial  # pyserial, optional

            port, _, baud = rest.partition(":")
            with serial.Serial(port, int(baud or 4800), timeout=1) as ser:
                while not self._stop.is_set():
                    line = ser.readline().decode("ascii", "ignore")
                    if line:
                        yield line
        else:
            raise ValueError(f"NMEA target must be udp:<port> or serial:<port>:<baud>, got {self.target!r}")

    def run(self) -> None:
        while not self._stop.is_set():
            try:
                for line in self._lines():
                    self.sentences += 1
                    pose = self.handle(line)
                    if pose is not None:
                        try:
                            self.manifest.telemetry.ingest(pose)
                        except ServiceError:
                            pass
            except Exception as exc:
                self.last_error = str(exc)
                log.warning("NMEA reader error (%s), retrying in 3 s", exc)
                self._stop.wait(3)
