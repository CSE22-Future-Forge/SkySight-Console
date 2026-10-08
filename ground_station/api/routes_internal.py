"""Pipeline hand-off, /internal/* (spec 7.3). 403 for any client but localhost."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from ground_station.api.auth import require_localhost
from ground_station.core.events import now_ms
from ground_station.core.models import DetectionIn, TelemetryIn
from ground_station.core.services import mission_view

router = APIRouter(prefix="/internal", dependencies=[Depends(require_localhost)])


@router.post("/detections")
def detections(body: DetectionIn, request: Request):
    return request.app.state.manifest.victims.ingest_detection(body)


@router.post("/telemetry")
def telemetry(body: TelemetryIn, request: Request):
    stored = request.app.state.manifest.telemetry.ingest(body)
    return {"stored": stored}


@router.get("/telemetry/latest")
def telemetry_latest(request: Request):
    """Newest UAV pose (from a telemetry reader or a POST above), so the pipeline can
    geolocate detections without its own MAVLink link. ``pose`` is null until one arrives."""
    m = request.app.state.manifest
    return {"pose": m.telemetry.latest, "server_time": now_ms(), "mission": mission_view(m.missions.active())}
