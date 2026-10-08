"""Coordinator GUI, headless (QT_QPA_PLATFORM=offscreen)."""

import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")

import cv2  # noqa: E402
import numpy as np  # noqa: E402
import pytest  # noqa: E402

from ground_station.core import geo  # noqa: E402

from .conftest import BASE_LAT, BASE_LON, add_device, cei, ingest  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from ground_station.ui.main_window import build_app

    return build_app([])


# ------------------------------------------------------------ GeoJSON builders ---
def test_victim_geojson_has_points_labels_and_error_circles(manifest):
    from ground_station.ui.map_view import victims_geojson

    a, _ = add_device(manifest, manifest.alpha)
    vid = ingest(manifest, track_id=1, geo_error_m=12)["victim_id"]
    manifest.victims.accept(vid, a, cei())
    teams = {t["id"]: t for t in manifest.teams.list()}
    data = victims_geojson(manifest.victims.list(), teams)
    point = data["points"]["features"][0]
    assert point["properties"] == {"id": vid, "status": "ongoing", "label": "V-0001 · A1"}
    assert point["geometry"]["coordinates"] == pytest.approx([BASE_LON, BASE_LAT])
    ring = data["errors"]["features"][0]["geometry"]["coordinates"][0]
    assert ring[0] == ring[-1]
    for lon, lat in ring[:-1]:
        assert geo.haversine_m(BASE_LAT, BASE_LON, lat, lon) == pytest.approx(12, abs=0.05)


def test_team_geojson_trail_and_staleness(manifest):
    from ground_station.ui.map_view import teams_geojson

    teams = manifest.teams.list()
    alpha = next(t for t in teams if t["name"] == "Alpha")
    data = teams_geojson(teams, {alpha["id"]: [(7.29, 80.63), (7.291, 80.631)]}, lambda t: True)
    assert data["trails"]["features"][0]["properties"]["color"] == "#9e9e9e"  # stale -> grey
    assert data["points"]["features"] == []  # no fix yet


def test_uav_geojson():
    from ground_station.ui.map_view import uav_geojson

    f = uav_geojson({"lat": 7.3, "lon": 80.6, "alt_agl_m": 61.4, "heading_deg": 135})
    assert f["properties"] == {"heading": 135, "label": "UAV 61 m"}
    assert uav_geojson(None) is None


# ------------------------------------------------------------------- panels ---
def test_manifest_panel_sorts_by_urgency_and_filters(qapp, manifest):
    from ground_station.ui.manifest_panel import ID_ROLE, ManifestPanel

    ids = [ingest(manifest, track_id=i, lat=7.29 + i * 0.01)["victim_id"] for i in range(3)]
    manifest.victims.dismiss(ids[0])
    manifest.missions.set_alert_mode("verify")
    pending = ingest(manifest, track_id=9, lat=7.5)["victim_id"]

    panel = ManifestPanel()
    teams = {t["id"]: t for t in manifest.teams.list()}
    panel.set_victims(manifest.victims.list(), teams)
    shown = [panel.proxy.index(r, 0).data(ID_ROLE) for r in range(panel.proxy.rowCount())]
    assert shown[0] == pending and ids[0] not in shown  # pending first, dismissed hidden by default
    assert "1 pending" in panel.counts.text()

    panel.chk_dismissed.setChecked(True)
    assert panel.proxy.rowCount() == 4

    got = []
    panel.victimSelected.connect(got.append)
    panel.table.selectRow(1)
    assert got == [panel.proxy.index(1, 0).data(ID_ROLE)]


@pytest.mark.parametrize("setup,expected", [
    ("open", ["Dismiss (false alarm)", "Assign to team"]),
    ("pending", ["Verify - alert teams", "Dismiss (false alarm)"]),
    ("ongoing", ["Release to open", "Reassign team", "Mark rescued"]),
    ("dismissed", ["Restore"]),
])
def test_detail_panel_offers_state_machine_actions(qapp, manifest, setup, expected):
    from PySide6.QtWidgets import QPushButton

    from ground_station.ui.detail_panel import DetailPanel

    if setup == "pending":
        manifest.missions.set_alert_mode("verify")
    vid = ingest(manifest, track_id=1)["victim_id"]
    if setup == "ongoing":
        manifest.victims.assign(vid, manifest.alpha["id"])
    elif setup == "dismissed":
        manifest.victims.dismiss(vid)

    panel = DetailPanel(manifest)
    panel.show_victim(vid)
    labels = [panel.actions_box.itemAt(i).widget().text() for i in range(panel.actions_box.count())
              if isinstance(panel.actions_box.itemAt(i).widget(), QPushButton)]
    assert sorted(labels) == sorted(expected)
    assert panel.code.text() == "V-0001"
    assert panel.size_buttons["frame"].isEnabled() and not panel.size_buttons["thermal"].isEnabled()


def test_detail_panel_verify_runs_service(qapp, manifest):
    from ground_station.ui.detail_panel import DetailPanel

    manifest.missions.set_alert_mode("verify")
    vid = ingest(manifest, track_id=1)["victim_id"]
    panel = DetailPanel(manifest)
    panel.show_victim(vid)
    changed = []
    panel.changed.connect(lambda: changed.append(1))
    panel._run("verify")
    assert manifest.victims.get(vid)["status"] == "open" and changed == [1]
    assert panel.chip.text() == "OPEN"


@pytest.mark.parametrize("active", [True, False])
def test_team_dialog_values_create_a_team(qapp, manifest, active):
    """Regression: the dialog's values (incl. is_active) must be accepted by TeamService.create."""
    from ground_station.ui.teams_panel import TeamDialog

    dlg = TeamDialog(default_color="#2ca02c")
    dlg.name.setText("Charlie")
    dlg.call_sign.setText("C1")
    dlg.active.setChecked(active)
    team = manifest.teams.create(**dlg.values())
    assert (team["name"], team["call_sign"], team["color_hex"], team["is_active"]) == \
        ("Charlie", "C1", "#2ca02c", 1 if active else 0)

    edit = TeamDialog(team=team)
    edit.name.setText("Charlie-2")
    assert manifest.teams.update(team["id"], **edit.values())["name"] == "Charlie-2"


def _decode_qr(pixmap) -> str:
    from PySide6.QtGui import QImage

    img = pixmap.toImage().convertToFormat(QImage.Format_RGB32)
    arr = np.frombuffer(img.constBits(), np.uint8).reshape(img.height(), img.bytesPerLine() // 4, 4)[:, :, :3]
    arr = np.ascontiguousarray(arr)
    # OpenCV's basic detector misses ~1 in 300 valid codes; the ArUco-based one decodes them all.
    text = cv2.QRCodeDetector().detectAndDecode(arr)[0]
    return text or cv2.QRCodeDetectorAruco().detectAndDecode(arr)[0]


def test_pairing_qr_decodes_to_payload(qapp, manifest):
    from ground_station.ui.pairing_dialog import PairingDialog

    dlg = PairingDialog(manifest, "http://192.168.50.10:8000", manifest.alpha["id"])
    for _ in range(25):  # every random token must produce a scannable code
        payload = json.loads(_decode_qr(dlg.qr.pixmap()))
        assert payload == {"server_url": "http://192.168.50.10:8000",
                           "pairing_token": dlg.payload["pairing_token"], "ssid": "SAR-NET"}
        dlg.regenerate()
    dlg.close()


def test_main_window_builds_and_refreshes(qapp, manifest):
    import socket

    from ground_station.api.server import ServerThread
    from ground_station.ui.main_window import MainWindow, dispose

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    srv = ServerThread(manifest, host="127.0.0.1", port=port, advertise=False)
    assert srv.start_and_wait()
    try:
        w = MainWindow(manifest, srv)
        vid = ingest(manifest, track_id=1)["victim_id"]
        qapp.processEvents()
        w._refresh()
        assert w.manifest_panel.model.rowCount() == 1
        w.select_victim(vid, fly=True)
        assert w.detail.victim_id == vid and w.map.bridge._cache["selection"] == vid
        cached = w.map.bridge._cache["victims"]["points"]["features"]
        assert [f["properties"]["id"] for f in cached] == [vid]
        assert "Demo" not in w.mission_label.text() and "Test mission" in w.mission_label.text()
        dispose(w)
    finally:
        srv.stop()
