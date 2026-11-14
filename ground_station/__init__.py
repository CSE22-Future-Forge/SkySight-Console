"""SAR Manifest ground station — offline rescue manifest server + coordinator GUI.

See docs/ARCHITECTURE.md. The laptop is the single source of truth: it owns the
SQLite manifest, serves the rescue phones over the SAR-NET LAN and accepts
confirmed detections from the SkySight pipeline on localhost.
"""

VERSION = "0.1.0"
