"""Map widget: QWebEngineView showing web/map.html, plus the QWebChannel bridge (spec 4.4).

The page is served by the ground station itself (/web/map.html) so the .pmtiles
archive, style, glyphs and page share one origin. qwebchannel.js comes out of Qt's
own resources and is injected before the page's scripts run.
"""

from __future__ import annotations

import json
import math
import time

from PySide6.QtCore import QFile, QIODevice, QObject, Qt, QUrl, Signal, Slot
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineScript
from PySide6.QtWebEngineWidgets import QWebEngineView

from ground_station.core import geo
from ground_station.ui import theme

ERROR_CIRCLE_POINTS = 40
TRAIL_MIN_POINTS = 2


# ------------------------------------------------------------ GeoJSON builders ---
def _fc(features):
    return {"type": "FeatureCollection", "features": features}


def circle_polygon(lat: float, lon: float, radius_m: float, points: int = ERROR_CIRCLE_POINTS) -> list:
    ring = [list(reversed(geo.destination(lat, lon, 360.0 * i / points, radius_m))) for i in range(points)]
    ring.append(ring[0])
    return [ring]


def victims_geojson(victims, teams_by_id: dict) -> dict:
    points, errors = [], []
    for v in victims:
        team = teams_by_id.get(v["assigned_team_id"]) if v["assigned_team_id"] else None
        label = v["display_code"]
        if team is not None and v["status"] in ("ongoing", "completed"):
            label += f" · {team['call_sign'] or team['name']}"
        props = {"id": v["id"], "status": v["status"], "label": label}
        points.append({"type": "Feature", "properties": props,
                       "geometry": {"type": "Point", "coordinates": [v["lon"], v["lat"]]}})
        if v["position_error_m"]:
            errors.append({"type": "Feature", "properties": {"id": v["id"], "status": v["status"]},
                           "geometry": {"type": "Polygon",
                                        "coordinates": circle_polygon(v["lat"], v["lon"], v["position_error_m"])}})
    return {"points": _fc(points), "errors": _fc(errors)}


def teams_geojson(teams, trails: dict, is_stale) -> dict:
    """``trails``: team_id -> [(lat, lon), ...] over the last 15 minutes."""
    points, lines = [], []
    now_ms = int(time.time() * 1000)
    for t in teams:
        stale = is_stale(t)
        color = theme.STALE_COLOR if stale else t["color_hex"]
        trail = trails.get(t["id"], [])
        if len(trail) >= TRAIL_MIN_POINTS:
            lines.append({"type": "Feature", "properties": {"team_id": t["id"], "color": color},
                          "geometry": {"type": "LineString", "coordinates": [[lon, lat] for lat, lon in trail]}})
        if t["last_lat"] is None:
            continue
        age_s = (now_ms - t["last_fix_at"]) // 1000 if t["last_fix_at"] else None
        detail = []
        if t["last_battery_pct"] is not None:
            detail.append(f"battery {t['last_battery_pct']}%")
        if age_s is not None:
            detail.append(f"fix {age_s}s ago" + (" (stale)" if stale else ""))
        if t["last_accuracy_m"] is not None:
            detail.append(f"±{t['last_accuracy_m']:.0f} m")
        points.append({"type": "Feature",
                       "properties": {"id": t["id"], "name": t["name"], "label": t["call_sign"] or t["name"],
                                      "color": color, "detail": ", ".join(detail)},
                       "geometry": {"type": "Point", "coordinates": [t["last_lon"], t["last_lat"]]}})
    return {"points": _fc(points), "trails": _fc(lines)}


def uav_geojson(pose: dict | None) -> dict | None:
    if not pose or pose.get("lat") is None:
        return None
    alt = pose.get("alt_agl_m")
    return {"type": "Feature",
            "properties": {"heading": pose.get("heading_deg") or 0.0,
                           "label": f"UAV {alt:.0f} m" if alt is not None else "UAV"},
            "geometry": {"type": "Point", "coordinates": [pose["lon"], pose["lat"]]}}


def bounds_of(victims) -> list | None:
    pts = [(v["lat"], v["lon"]) for v in victims]
    if not pts:
        return None
    lats, lons = [p[0] for p in pts], [p[1] for p in pts]
    pad = 0.002
    return [[min(lons) - pad, min(lats) - pad], [max(lons) + pad, max(lats) + pad]]


# ----------------------------------------------------------------- the bridge ---
class MapBridge(QObject):
    """Registered as ``bridge`` on the page's QWebChannel. See map.js for the protocol."""

    victimsChanged = Signal(str)
    teamsChanged = Signal(str)
    uavChanged = Signal(str)
    selectionChanged = Signal(str)
    flyToRequested = Signal(float, float, float)
    fitRequested = Signal(str)

    # Python-side notifications
    victimClicked = Signal(str)
    addVictimRequested = Signal(float, float)
    ready = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cache: dict[str, object] = {}
        self.is_ready = False

    def push(self, kind: str, payload) -> None:
        self._cache[kind] = payload
        text = json.dumps(payload)
        {"victims": self.victimsChanged, "teams": self.teamsChanged, "uav": self.uavChanged}[kind].emit(text)

    def select(self, victim_id: str) -> None:
        self._cache["selection"] = victim_id or ""
        self.selectionChanged.emit(victim_id or "")

    @Slot(result=str)
    def snapshot(self) -> str:
        return json.dumps(self._cache)

    @Slot()
    def mapReady(self):
        self.is_ready = True
        self.ready.emit()

    @Slot(str)
    def onVictimClicked(self, victim_id):
        self.victimClicked.emit(victim_id)

    @Slot(float, float)
    def onAddVictimAt(self, lat, lon):
        self.addVictimRequested.emit(lat, lon)


class _Page(QWebEnginePage):
    def javaScriptConsoleMessage(self, level, message, line, source):  # surface JS errors in the log
        if level == QWebEnginePage.JavaScriptConsoleMessageLevel.ErrorMessageLevel:
            import logging

            logging.getLogger("ground_station.map").warning("JS %s:%s %s", source, line, message)


def _qwebchannel_js() -> str:
    f = QFile(":/qtwebchannel/qwebchannel.js")
    if not f.open(QIODevice.ReadOnly):
        raise RuntimeError("qwebchannel.js not found in Qt resources")
    try:
        return bytes(f.readAll()).decode("utf-8")
    finally:
        f.close()


class MapView(QWebEngineView):
    victimClicked = Signal(str)
    addVictimRequested = Signal(float, float)

    def __init__(self, base_url: str, parent=None):
        super().__init__(parent)
        self.setContextMenuPolicy(Qt.NoContextMenu)  # the page has its own right-click menu
        page = _Page(self)
        self.setPage(page)

        script = QWebEngineScript()
        script.setName("qwebchannel")
        script.setSourceCode(_qwebchannel_js())
        script.setInjectionPoint(QWebEngineScript.DocumentCreation)
        script.setWorldId(QWebEngineScript.MainWorld)
        script.setRunsOnSubFrames(False)
        page.scripts().insert(script)

        self.bridge = MapBridge(self)
        self.channel = QWebChannel(page)
        self.channel.registerObject("bridge", self.bridge)
        page.setWebChannel(self.channel)
        self.bridge.victimClicked.connect(self.victimClicked)
        self.bridge.addVictimRequested.connect(self.addVictimRequested)

        self.load(QUrl(base_url.rstrip("/") + "/web/map.html?gui=1"))

    def dispose(self) -> None:
        """Delete the page before the view: the page must never outlive QApplication."""
        page = self.page()
        self.setPage(None)
        if page is not None:
            page.deleteLater()

    # Each push is cached on the bridge, so updates made before the page loads are replayed.
    def set_victims(self, victims, teams_by_id):
        self.bridge.push("victims", victims_geojson(victims, teams_by_id))

    def set_teams(self, teams, trails, is_stale):
        self.bridge.push("teams", teams_geojson(teams, trails, is_stale))

    def set_uav(self, pose):
        self.bridge.push("uav", uav_geojson(pose))

    def select(self, victim_id):
        self.bridge.select(victim_id)

    def fly_to(self, lat, lon, zoom=16.0):
        if not (math.isfinite(lat) and math.isfinite(lon)):
            return
        self.bridge.flyToRequested.emit(float(lat), float(lon), float(zoom))

    def fit(self, victims):
        b = bounds_of(victims)
        if b:
            self.bridge.fitRequested.emit(json.dumps(b))
