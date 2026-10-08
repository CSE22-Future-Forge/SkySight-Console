"""Phone REST API, /api/v1 (spec 7.1). Handlers are sync: FastAPI runs them in its
threadpool and each worker thread gets its own SQLite connection."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import FileResponse

from ground_station import VERSION
from ground_station.api.auth import client_ip, require_device
from ground_station.core import events as ev
from ground_station.core.models import CompleteIn, ActionIn, LocationsIn, PairIn, ReceiptIn, ReleaseIn
from ground_station.core.services import mission_view

router = APIRouter(prefix="/api/v1")


def _m(request: Request):
    return request.app.state.manifest


@router.post("/pair")
def pair(body: PairIn, request: Request):
    return _m(request).devices.pair(body, client_ip(request))


@router.get("/health")
def health(request: Request):
    return {"server_time": ev.now_ms(), "version": VERSION, "mission": mission_view(_m(request).missions.active())}


@router.get("/me")
def me(request: Request, device=Depends(require_device)):
    return _m(request).devices.me(device)


@router.get("/victims")
def victims(request: Request, since_seq: int = 0, device=Depends(require_device)):
    return _m(request).victims.visible_for_team(device["team_id"], since_seq)


@router.get("/victims/{victim_id}")
def victim(victim_id: str, request: Request, device=Depends(require_device)):
    return _m(request).victims.detail_for_team(victim_id, device["team_id"])


@router.get("/victims/{victim_id}/image")
def victim_image(victim_id: str, request: Request, size: str = "thumb", device=Depends(require_device)):
    path = _m(request).victims.image_path(victim_id, size, team_id=device["team_id"])
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})


@router.post("/victims/{victim_id}/accept")
def accept(victim_id: str, body: ActionIn, request: Request, device=Depends(require_device)):
    return _m(request).victims.accept(victim_id, device, body.client_event_id, body.client_ts)


@router.post("/victims/{victim_id}/release")
def release(victim_id: str, body: ReleaseIn, request: Request, device=Depends(require_device)):
    return _m(request).victims.release(victim_id, device, body.client_event_id, body.client_ts, body.reason)


@router.post("/victims/{victim_id}/complete")
def complete(victim_id: str, body: CompleteIn, request: Request, device=Depends(require_device)):
    return _m(request).victims.complete(victim_id, device, body.client_event_id, body.client_ts,
                                        body.people_count, body.note)


@router.post("/locations")
def locations(body: LocationsIn, request: Request, device=Depends(require_device)):
    return _m(request).teams.add_locations(device, [p.model_dump() for p in body.points])


@router.post("/alerts/{victim_id}/receipt", status_code=204)
def receipt(victim_id: str, body: ReceiptIn, request: Request, device=Depends(require_device)):
    _m(request).victims.add_receipt(victim_id, device, body.delivered_at, body.opened_at)
    return Response(status_code=204)


@router.get("/sync")
def sync(request: Request, since_seq: int = 0, device=Depends(require_device)):
    return _m(request).sync.events_for_team(device["team_id"], since_seq)
