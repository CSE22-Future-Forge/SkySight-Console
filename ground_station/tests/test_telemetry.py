"""MAVLink and NMEA pose parsing."""

import pytest
from pymavlink.dialects.v20 import common as mav

from ground_station.telemetry.mavlink_reader import MavlinkReader
from ground_station.telemetry.nmea_reader import NmeaReader


def nmea(body: str) -> str:
    """Wrap a sentence body with $ and its XOR checksum."""
    checksum = 0
    for ch in body:
        checksum ^= ord(ch)
    return f"${body}*{checksum:02X}"


def test_mavlink_pose_from_position_attitude_and_battery(manifest):
    r = MavlinkReader(manifest)
    assert r.handle(mav.MAVLink_attitude_message(0, 0.1, -0.2, 1.0, 0, 0, 0)) is None
    r.handle(mav.MAVLink_sys_status_message(0, 0, 0, 0, 12000, 0, 77, 0, 0, 0, 0, 0, 0, 0, 0, 0))
    pose = r.handle(mav.MAVLink_global_position_int_message(0, int(7.2906e7), int(80.6337e7), 560_000, 60_000,
                                                             300, 400, 0, 13500))
    assert pose.lat == pytest.approx(7.2906) and pose.lon == pytest.approx(80.6337)
    assert pose.alt_agl_m == 60 and pose.heading_deg == 135 and pose.battery_pct == 77
    assert pose.ground_speed_mps == pytest.approx(5.0) and pose.pitch_deg == pytest.approx(-11.46, abs=0.01)


def test_mavlink_ignores_no_fix(manifest):
    r = MavlinkReader(manifest)
    assert r.handle(mav.MAVLink_global_position_int_message(0, 0, 0, 0, 0, 0, 0, 0, 65535)) is None


def test_nmea_gga_with_rmc_course(manifest):
    r = NmeaReader(manifest)
    assert r.handle(nmea("GPRMC,123519,A,0717.436,N,08038.022,E,010.0,084.4,071026,,")) is None
    pose = r.handle(nmea("GPGGA,123520,0717.436,N,08038.022,E,1,08,0.9,545.4,M,46.9,M,,"))
    assert pose.lat == pytest.approx(7.29060, abs=1e-5) and pose.lon == pytest.approx(80.63370, abs=1e-5)
    assert pose.alt_msl_m == pytest.approx(545.4) and pose.heading_deg == pytest.approx(84.4)
    assert pose.ground_speed_mps == pytest.approx(5.144, abs=0.01)


def test_nmea_rejects_no_fix_and_garbage(manifest):
    r = NmeaReader(manifest)
    assert r.handle(nmea("GPGGA,123520,,,,,0,00,,,M,,M,,")) is None
    assert r.handle("not nmea at all") is None
