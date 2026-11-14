# CLAUDE.md — SAR Manifest (FYP prototype)

You are building the rescue-coordination part of a UAV search-and-rescue final-year project:
an **offline ground-station app for a Windows laptop** (Python + PySide6, packaged as an .exe)
and an **Android app for rescue teams** (React Native + Expo). They talk over a local Wi-Fi network with
**no internet at all**.

Before writing code, read:
- `docs/ARCHITECTURE.md` — the full specification (network, processes, API, state machine,
  de-duplication, offline maps, mobile behaviour, acceptance tests).
- `docs/schema.sql` — the laptop database schema. It is the source of truth; do not change
  column names without updating the spec and adding a migration.

## Non-negotiable constraints

1. **Zero internet at runtime.** No CDN `<script>`/`<link>` tags, no Google Fonts, no Firebase/FCM,
   no online tile URLs, no telemetry/analytics. Every JS file, font glyph, sprite and map tile is
   bundled locally. If a library needs internet at runtime, don't use it.
2. **The laptop is authoritative.** Only `core/services.py` + `core/state_machine.py` change victim
   state. GUI and API both go through the service layer.
3. **Status changes use one conditional UPDATE** (see ARCHITECTURE §5). Never read-then-write.
4. **Every state change writes an `events` row before pushing** it to WebSocket clients or the GUI.
5. **Never touch Qt widgets from the server thread.** Use Qt signals from the event bus.
6. **All SQL in `ground_station/db/repo.py`.** One SQLite connection per thread; apply the PRAGMAs
   in `schema.sql` on every connection.
7. **Phone actions are idempotent** via `client_event_id`; GPS points via `client_point_id`.
8. **Visibility filtering happens on the server** (ARCHITECTURE §7.5), never only in the app.
9. The detection pipeline (PyTorch/YOLO) is **out of scope** here and must not be bundled into the
   ground-station exe. Integrate only through `POST /internal/detections`; use `tools/mock_feed.py`.

## Conventions

- Python 3.11+, type hints, Pydantic v2 models, FastAPI, Uvicorn, PySide6, `sqlite3` (or SQLAlchemy 2.0 Core — pick one and stay consistent).
- Timestamps: UTC epoch **milliseconds** (int). Coordinates: WGS84 floats. IDs: UUIDv4 strings.
- Status strings lowercase: `pending`, `open`, `ongoing`, `completed`, `dismissed`.
- Runtime data dir: `%LOCALAPPDATA%\SARManifest\` (fallback `~/.sarmanifest/` on non-Windows for dev).
- Settings come from the `settings` table; never hard-code the tunables listed there.
- React Native (Expo SDK 57, TypeScript strict): `expo-sqlite` for local DB, `fetch` for REST, the small store in
  `src/state/store.ts` for state, native Android code only in `modules/sar-native` — be consistent.
- Write tests alongside code: `pytest` + `httpx`/FastAPI TestClient for the server; Jest unit tests for app logic (outbox, sync, geo math).

## Build order (one milestone at a time; stop and summarise after each)

**M1 — Server core (no GUI)**
- Data dir + DB init from `schema.sql`, settings loader, repo layer.
- Services: missions, teams, pairing (QR token), devices, victims, state machine, de-dup, events.
- FastAPI: `/api/v1/*`, `/internal/*`, `/ws` with replay + ping, `/maps/*` with HTTP Range.
- `tools/mock_feed.py`: posts fake detections (configurable centre lat/lon, people count, repeat passes, jitter) and telemetry.
- Tests: claim race (concurrent accepts → one winner), idempotent retries, visibility filtering, de-dup cases, replay after reconnect, internal routes reject non-localhost.
- Done when: `pytest` passes and a WebSocket client script receives alerts from mock_feed.

**M2 — Mobile app core**
- Pairing via QR, Wi-Fi binding (sar-native Kotlin module), secure token storage.
- Foreground service with WebSocket, reconnect with `since_seq`, ack, ping/pong.
- Alarm notifications (full-screen intent, siren, cancel on `victim.hidden`), receipts.
- Victim list/detail, Accept / Release / Complete with outbox + optimistic UI + 409 rollback.
- Location service + offline buffer + batch upload.
- Done when: two phones (or emulators) against M1 + mock_feed pass the claim-race and replay tests.

**M3 — Offline maps**
- `tools/build_map.md` with Planetiler steps; local `style.json`, glyphs, sprites.
- Phone: map download at pairing (resumable, sha256), MapLibre with local PMTiles, own location, victim markers, distance + bearing.
- Laptop web map assets in `ui/web/` (MapLibre GL JS + pmtiles protocol, all local).
- Done when: phone in airplane mode (GPS on) shows map + labels + position.

**M4 — Coordinator GUI (PySide6)**
- Main window, map view with QWebChannel bridge, manifest table, detail panel, teams panel,
  pairing dialog (QR), settings, mission start/stop, alert mode, coordinator actions.
- Done when: full flow works end-to-end with mock_feed + 2 phones, laptop shows all teams/victims live.

**M5 — Telemetry + pipeline integration**
- MAVLink/NMEA readers, UAV marker; document the `/internal/detections` contract for the pipeline team.

**M6 — Packaging**
- PyInstaller one-folder spec (include QtWebEngine resources and `ui/web/`), Inno Setup installer with firewall rule.
- Signed release APK.
- Done when: fresh Windows machine, no Python installed, no internet → install, run, pair a phone, receive an alert.

## Open decisions (ask before assuming otherwise)

- Visibility rule: assumed every team sees all **open** victims + its own. Alternative: coordinator dispatches.
- HTTP for prototype; HTTPS with pinned self-signed cert is optional.
- The phone app is React Native + Expo (the team switched from Flutter); keep native code in `modules/sar-native`.

## Useful commands (create these as you go)

```
# server dev
cd ground_station && python -m pip install -r requirements.txt
python app.py                 # GUI + server
python -m api.server          # server only (headless dev)
pytest -q

# mock pipeline
python tools/mock_feed.py --server http://127.0.0.1:8000 --lat 6.7951 --lon 79.9009 --people 5 --passes 3

# phone
cd mobile_app && npm install && npm test && bash tools/build_android.sh
```
