"""Paths (%LOCALAPPDATA%/SARManifest) and the settings loader."""

from __future__ import annotations

import os
import threading
from pathlib import Path

APP_DIR_NAME = "SARManifest"
SSID = "SAR-NET"

# Typed defaults. Values live in the ``settings`` table (seeded by schema.sql);
# the type of each default decides how the stored string is parsed.
DEFAULTS: dict[str, object] = {
    "server_port": 8000,
    "dedup_radius_min_m": 15.0,
    "dedup_error_multiplier": 2.0,
    "dedup_window_min": 15.0,
    "rescuer_radius_m": 20.0,
    "team_stale_after_s": 120,
    "ws_ping_interval_s": 15,
    "ws_dead_after_s": 45,
    "pairing_token_ttl_min": 10,
    "alert_repeat_s": 30,
    "gps_max_accuracy_m": 50.0,
    "map_file": "maps/srilanka.pmtiles",
    # Not in schema.sql's seed list: optional overrides.
    "server_host_ip": "",
    "telemetry_source": "",  # see ground_station/telemetry/__init__.py
}


def data_dir() -> Path:
    override = os.environ.get("SAR_MANIFEST_DATA")
    if override:
        return Path(override)
    base = os.environ.get("LOCALAPPDATA")
    return (Path(base) if base else Path.home() / ".local" / "share") / APP_DIR_NAME


class Paths:
    def __init__(self, root: str | os.PathLike | None = None):
        self.root = Path(root) if root else data_dir()
        self.db = self.root / "manifest.db"
        self.maps = self.root / "maps"
        self.images = self.root / "images"
        self.logs = self.root / "logs"

    def ensure(self) -> "Paths":
        for p in (self.root, self.maps, self.images, self.logs):
            p.mkdir(parents=True, exist_ok=True)
        return self

    def map_file(self, settings: "Settings") -> Path:
        rel = Path(str(settings.get("map_file")))
        if rel.is_absolute():
            return rel
        own = self.root / rel
        # The installer (admin) ships the map machine-wide, as it cannot reach the operator's profile.
        shared = Path(os.environ.get("PROGRAMDATA", "")) / APP_DIR_NAME / rel if os.environ.get("PROGRAMDATA") else None
        if not own.is_file() and shared is not None and shared.is_file():
            return shared
        return own


class Settings:
    """Typed, cached view of the settings table."""

    def __init__(self, db):
        self.db = db
        self._cache: dict[str, str] | None = None
        self._lock = threading.Lock()

    def _load(self) -> dict[str, str]:
        with self._lock:
            if self._cache is None:
                from ground_station.db import repo

                self._cache = repo.get_settings(self.db.conn())
            return self._cache

    def get(self, key: str):
        default = DEFAULTS.get(key)
        raw = self._load().get(key)
        if raw is None:
            return default
        if isinstance(default, bool):
            return raw.lower() in ("1", "true", "yes")
        if isinstance(default, int):
            return int(float(raw))
        if isinstance(default, float):
            return float(raw)
        return raw

    def set(self, key: str, value) -> None:
        from ground_station.db import repo

        with self.db.tx() as conn:
            repo.set_setting(conn, key, str(value))
        with self._lock:
            self._cache = None

    def all(self) -> dict:
        keys = set(DEFAULTS) | set(self._load())
        return {k: self.get(k) for k in sorted(keys)}
