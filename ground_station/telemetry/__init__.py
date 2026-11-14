"""UAV pose readers (spec 4.1 "Telemetry reader"). Both feed TelemetryService.ingest,
which keeps the latest pose in memory and stores at most 1 Hz.

The ``telemetry_source`` setting picks one:
    mavlink:udpin:0.0.0.0:14550      MAVLink (pymavlink connection string after "mavlink:")
    mavlink:COM5:57600               MAVLink over a serial telemetry radio
    nmea:udp:10110                   NMEA sentences on a UDP port
    nmea:serial:COM3:4800            NMEA from a serial GPS (needs pyserial)
Empty = no reader (the pipeline may post /internal/telemetry itself).
"""

from __future__ import annotations


def start_reader(manifest, source: str):
    """Start and return the reader thread for ``source``, or None if it is empty."""
    source = (source or "").strip()
    if not source:
        return None
    kind, _, target = source.partition(":")
    if kind == "mavlink":
        from ground_station.telemetry.mavlink_reader import MavlinkReader

        reader = MavlinkReader(manifest, target)
    elif kind == "nmea":
        from ground_station.telemetry.nmea_reader import NmeaReader

        reader = NmeaReader(manifest, target)
    else:
        raise ValueError(f"unknown telemetry source {source!r} (expected mavlink:... or nmea:...)")
    reader.start()
    return reader
