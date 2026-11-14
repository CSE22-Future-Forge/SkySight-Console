"""Pydantic schemas for API requests and internal DTOs (spec section 7).

Responses are plain dicts built by the service layer; only inbound payloads are
validated here so a malformed phone or pipeline message is rejected at the edge.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator


# ------------------------------------------------------------------ internal ---
class BBox(BaseModel):
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    w: int = Field(gt=0)
    h: int = Field(gt=0)


class UavPose(BaseModel):
    lat: Optional[float] = None
    lon: Optional[float] = None
    alt_agl_m: Optional[float] = None
    heading_deg: Optional[float] = None
    gimbal_pitch_deg: Optional[float] = None


class DetectionImages(BaseModel):
    frame_path: Optional[str] = None
    frame_b64: Optional[str] = None
    thermal_crop_path: Optional[str] = None
    thermal_crop_b64: Optional[str] = None

    @model_validator(mode="after")
    def _need_frame(self):
        if not (self.frame_path or self.frame_b64):
            raise ValueError("images needs frame_path or frame_b64")
        return self


class DetectionIn(BaseModel):
    # Optional: the pipeline may not know the mission; the active one is used.
    mission_id: Optional[str] = None
    track_id: Optional[int] = None
    frame_ts: int
    modality: Literal["rgb", "thermal", "fused"]
    confidence: float = Field(ge=0.0, le=1.0)
    bbox: BBox
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    geo_error_m: Optional[float] = Field(default=None, ge=0)
    uav: Optional[UavPose] = None
    weather_tag: Optional[Literal["clear", "rain", "fog", "haze", "night"]] = None
    images: DetectionImages


class TelemetryIn(BaseModel):
    mission_id: Optional[str] = None
    ts: int
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    alt_msl_m: Optional[float] = None
    alt_agl_m: Optional[float] = None
    heading_deg: Optional[float] = None
    pitch_deg: Optional[float] = None
    roll_deg: Optional[float] = None
    gimbal_pitch_deg: Optional[float] = None
    ground_speed_mps: Optional[float] = None
    battery_pct: Optional[int] = None


# -------------------------------------------------------------------- mobile ---
class PairIn(BaseModel):
    pairing_token: str = Field(min_length=8)
    device_label: str = Field(min_length=1, max_length=80)
    team_id: Optional[str] = None
    app_version: Optional[str] = None
    os_version: Optional[str] = None


class ActionIn(BaseModel):
    client_event_id: str = Field(min_length=8, max_length=64)
    client_ts: Optional[int] = None


class ReleaseIn(ActionIn):
    reason: Optional[str] = Field(default=None, max_length=500)


class CompleteIn(ActionIn):
    people_count: Optional[int] = Field(default=None, ge=0, le=500)
    note: Optional[str] = Field(default=None, max_length=2000)


class LocationPoint(BaseModel):
    client_point_id: str = Field(min_length=8, max_length=64)
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    accuracy_m: Optional[float] = None
    altitude_m: Optional[float] = None
    speed_mps: Optional[float] = None
    heading_deg: Optional[float] = None
    battery_pct: Optional[int] = None
    fix_ts: int


class LocationsIn(BaseModel):
    points: list[LocationPoint] = Field(max_length=5000)


class ReceiptIn(BaseModel):
    delivered_at: Optional[int] = None
    opened_at: Optional[int] = None
