"""MAVLink pose reader (ArduPilot / PX4 via a telemetry radio or UDP from the GCS)."""

from __future__ import annotations

import logging
import math
import threading
import time

from ground_station.core.models import TelemetryIn
from ground_station.core.services import ServiceError

log = logging.getLogger(__name__)

WANTED = ["GLOBAL_POSITION_INT", "ATTITUDE", "SYS_STATUS", "VFR_HUD", "MOUNT_STATUS",
          "GIMBAL_DEVICE_ATTITUDE_STATUS"]


def quaternion_pitch_deg(q) -> float:
    w, x, y, z = q
    return math.degrees(math.asin(max(-1.0, min(1.0, 2 * (w * y - z * x)))))


class MavlinkReader(threading.Thread):
    def __init__(self, manifest, connection: str = "udpin:0.0.0.0:14550"):
        super().__init__(name="mavlink-reader", daemon=True)
        self.manifest = manifest
        self.connection = connection
        self.state: dict = {}
        self.messages = 0
        self.last_error: str | None = None
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def handle(self, msg) -> TelemetryIn | None:
        """Fold one MAVLink message into the pose; returns a pose on each position fix."""
        t = msg.get_type()
        s = self.state
        if t == "ATTITUDE":
            s["pitch_deg"] = math.degrees(msg.pitch)
            s["roll_deg"] = math.degrees(msg.roll)
        elif t == "SYS_STATUS" and msg.battery_remaining >= 0:
            s["battery_pct"] = int(msg.battery_remaining)
        elif t == "VFR_HUD":
            s["ground_speed_mps"] = float(msg.groundspeed)
        elif t == "MOUNT_STATUS":
            s["gimbal_pitch_deg"] = msg.pointing_a / 100.0
        elif t == "GIMBAL_DEVICE_ATTITUDE_STATUS":
            s["gimbal_pitch_deg"] = quaternion_pitch_deg(msg.q)
        elif t == "GLOBAL_POSITION_INT":
            if msg.lat == 0 and msg.lon == 0:
                return None  # no fix yet
            hdg = None if msg.hdg == 65535 else msg.hdg / 100.0
            return TelemetryIn(
                ts=int(time.time() * 1000), lat=msg.lat / 1e7, lon=msg.lon / 1e7,
                alt_msl_m=msg.alt / 1000.0, alt_agl_m=msg.relative_alt / 1000.0, heading_deg=hdg,
                pitch_deg=s.get("pitch_deg"), roll_deg=s.get("roll_deg"),
                gimbal_pitch_deg=s.get("gimbal_pitch_deg"),
                ground_speed_mps=s.get("ground_speed_mps", math.hypot(msg.vx, msg.vy) / 100.0),
                battery_pct=s.get("battery_pct"),
            )
        return None

    def run(self) -> None:
        from pymavlink import mavutil

        while not self._stop.is_set():
            try:
                conn = mavutil.mavlink_connection(self.connection, source_system=254)
                while not self._stop.is_set():
                    msg = conn.recv_match(type=WANTED, blocking=True, timeout=1)
                    if msg is None:
                        continue
                    self.messages += 1
                    pose = self.handle(msg)
                    if pose is not None:
                        try:
                            self.manifest.telemetry.ingest(pose)
                        except ServiceError:
                            pass  # no active mission: keep listening
            except Exception as exc:  # radio unplugged etc.: retry
                self.last_error = str(exc)
                log.warning("MAVLink reader error (%s), retrying in 3 s", exc)
                self._stop.wait(3)
