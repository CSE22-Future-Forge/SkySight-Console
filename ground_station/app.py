"""Ground station entry point: init data dir + DB, start the API thread, start Qt.

    python -m ground_station.app                 # GUI (coordinator console)
    python -m ground_station.app --headless      # server only, Ctrl+C to stop
    python -m ground_station.app --headless --seed-demo --pair
"""

from __future__ import annotations

import argparse
import json
import logging
import logging.handlers
import sys
import threading

from ground_station import VERSION
from ground_station.config import Paths
from ground_station.core.services import Manifest

log = logging.getLogger("ground_station")

DEMO_TEAMS = (("Alpha", "A1", "#1f77b4"), ("Bravo", "B1", "#ff7f0e"))


def setup_logging(paths: Paths) -> None:
    handler = logging.handlers.RotatingFileHandler(paths.logs / "ground_station.log", maxBytes=5_000_000,
                                                   backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    console = logging.StreamHandler()
    console.setLevel(logging.WARNING)
    root.addHandler(console)


def seed_demo(manifest: Manifest) -> None:
    if not manifest.teams.list():
        for name, call_sign, color in DEMO_TEAMS:
            manifest.teams.create(name, call_sign, color)
    if manifest.missions.active() is None:
        manifest.missions.start("Demo mission", "Kandy")


def parse_args(argv=None):
    p = argparse.ArgumentParser(prog="ground_station", description=f"SAR Manifest ground station {VERSION}")
    p.add_argument("--headless", action="store_true", help="run the server without the GUI")
    p.add_argument("--data-dir", help="override %%LOCALAPPDATA%%/SARManifest")
    p.add_argument("--port", type=int, help="override the server_port setting")
    p.add_argument("--seed-demo", action="store_true", help="create demo teams and a mission if missing")
    p.add_argument("--pair", action="store_true", help="print a one-time pairing QR payload (headless)")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    paths = Paths(args.data_dir).ensure()
    setup_logging(paths)
    manifest = Manifest(paths)
    if args.port:
        manifest.settings.set("server_port", args.port)
    if args.seed_demo:
        seed_demo(manifest)

    from ground_station.api.server import ServerThread, server_url

    server = ServerThread(manifest)
    if not server.start_and_wait():
        log.error("API server failed to start on port %s: %s", server.port, server.error)
        print(f"[ERROR] could not start the server on port {server.port}. Is another copy running?", file=sys.stderr)
        manifest.close()
        return 1
    log.info("ground station %s serving %s (data %s)", VERSION, server_url(manifest), paths.root)

    from ground_station.telemetry import start_reader

    reader = None
    try:
        reader = start_reader(manifest, manifest.settings.get("telemetry_source"))
    except ValueError as exc:
        log.error("telemetry reader not started: %s", exc)

    try:
        if args.headless:
            return _run_headless(manifest, args)
        from ground_station.ui.main_window import run_gui

        return run_gui(manifest, server)
    finally:
        if reader is not None:
            reader.stop()
        server.stop()
        manifest.close()


def _run_headless(manifest: Manifest, args) -> int:
    from ground_station.api.server import server_url

    url = server_url(manifest)
    print(f"SAR Manifest {VERSION} - serving {url}  (data: {manifest.paths.root})", flush=True)
    if args.pair:
        pairing = manifest.devices.create_pairing(server_url=url)
        print("Pairing QR payload (valid", manifest.settings.get("pairing_token_ttl_min"), "min):")
        print(json.dumps(pairing["qr_payload"]), flush=True)
    print("Ctrl+C to stop.", flush=True)
    stop = threading.Event()
    try:
        while not stop.wait(0.5):  # short waits: an untimed wait ignores Ctrl+C on Windows
            pass
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
