# SAR Manifest ground station

The laptop side of the live rescue manifest (see [docs/ARCHITECTURE.md](../docs/ARCHITECTURE.md)).
It is the single source of truth: it receives confirmed detections from SkySight, de-duplicates
them into victims, runs the coordinator console, and serves the rescue phones over the SAR-NET LAN.

## Run

```powershell
python -m pip install -r ground_station\requirements.txt   # into .venv, once
.\run_ground_station.bat                                    # coordinator GUI
.\run_ground_station.bat --headless --seed-demo --pair      # server only, with demo teams + a pairing code
```

Data is stored in `%LOCALAPPDATA%\SARManifest`: `manifest.db`, `images\`, `maps\`, `logs\` and `archives\`.

## First mission

1. **Map:** run `bash tools/build_map.sh` once (see [tools/build_map.md](../tools/build_map.md)).
   Without a map, the console shows a plain background and the markers still work.
2. **Teams tab:** add the teams with **+ Team**.
3. **Mission:** click **Start mission** and choose the alert mode:
   - **Auto** alerts the phones immediately.
   - **Verify** holds each new detection (purple marker) until you verify it.
4. **Pair phones:** click **Pair phone** and have each phone scan the QR code while on SAR-NET.
5. **Detections:** in SkySight Console, tick **Send confirmed people to SAR Manifest**. To test
   without a drone, run `python tools/mock_feed.py --count 20 --telemetry`.
6. **Coordinate:** select a victim in the manifest or on the map, then use the actions in the
   Victim panel. Right-click the map to add a victim reported by people on the ground.
7. **Finish:** click **End mission**. You can then archive the mission to a zip file, optionally
   deleting the images.

UAV pose: set `telemetry_source` in **Settings** (for example `mavlink:udpin:0.0.0.0:14550`).
SkySight reads the latest pose from the ground station to geolocate what it sees.

## Test

```powershell
.venv\Scripts\python -m pytest ground_station\tests -q
```

The suite covers the claim race, idempotency, de-duplication, visibility events, WebSocket replay
and latency against a real uvicorn server, the SkySight hand-off and the GUI (headless).

## Package

```powershell
.venv\Scripts\python -m pip install pyinstaller
.venv\Scripts\python -m PyInstaller packaging\ground_station.spec --noconfirm   # -> dist\SARManifest\
"C:\Program Files (x86)\Inno Setup 6\ISCC.exe" packaging\installer.iss         # -> dist\SARManifest-Setup-*.exe
```

The installer adds a firewall rule for TCP 8000 and mDNS on **private** networks only. It ships
`srilanka.pmtiles` if you place that file next to `installer.iss`.
