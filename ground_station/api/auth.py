"""Request guards: device bearer tokens and the localhost-only internal API.

Device tokens are 32 random bytes (url-safe base64) shown to the phone once at
pairing; only their SHA-256 is stored (see DeviceService).
"""

from __future__ import annotations

from fastapi import Request

from ground_station.core.services import ServiceError

LOCALHOST = frozenset({"127.0.0.1", "::1"})


def client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def bearer_token(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    return token.strip() if scheme.lower() == "bearer" and token.strip() else None


def require_device(request: Request):
    """FastAPI dependency: the authenticated, non-revoked device row."""
    manifest = request.app.state.manifest
    return manifest.devices.authenticate(bearer_token(request), client_ip(request))


def require_localhost(request: Request) -> None:
    """Spec 10: /internal/* answers 403 to anything but the pipeline on this laptop."""
    if client_ip(request) not in request.app.state.internal_hosts:
        raise ServiceError("forbidden", "Internal API is only available on localhost", 403)
