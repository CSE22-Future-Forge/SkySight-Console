"""FastAPI app factory and the uvicorn background thread (spec 4.1).

Uvicorn runs on its own thread with its own asyncio loop so the Qt GUI keeps the
main thread. ``ServerThread`` also advertises ``_sarmanifest._tcp.local.`` over
mDNS as the discovery fallback (spec 3).
"""

from __future__ import annotations

import asyncio
import logging
import socket
import threading
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Request, WebSocket
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from ground_station import VERSION
from ground_station.api import routes_internal, routes_maps, routes_mobile
from ground_station.api.auth import LOCALHOST
from ground_station.api.ws_hub import WsHub
from ground_station.core.services import ServiceError

log = logging.getLogger(__name__)

MDNS_TYPE = "_sarmanifest._tcp.local."
PREFERRED_SUBNET = "192.168.50."


def create_app(manifest, internal_hosts=LOCALHOST) -> FastAPI:
    hub = WsHub(manifest)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        hub.attach_loop(asyncio.get_running_loop())
        yield
        await hub.shutdown()

    app = FastAPI(title="SAR Manifest ground station", version=VERSION, lifespan=lifespan,
                  docs_url="/docs", redoc_url=None)
    app.state.manifest = manifest
    app.state.hub = hub
    app.state.internal_hosts = frozenset(internal_hosts)

    @app.exception_handler(ServiceError)
    async def _service_error(_request: Request, exc: ServiceError):
        return JSONResponse(exc.body(), status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_request: Request, exc: RequestValidationError):
        details = [{"loc": list(e.get("loc", ())), "msg": e.get("msg")} for e in exc.errors()]
        return JSONResponse({"error": "validation_error", "message": "Request body is invalid",
                             "details": details}, status_code=422)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_request: Request, exc: StarletteHTTPException):
        return JSONResponse({"error": f"http_{exc.status_code}", "message": str(exc.detail)},
                            status_code=exc.status_code)

    app.include_router(routes_mobile.router)
    app.include_router(routes_internal.router)
    app.include_router(routes_maps.router)

    @app.websocket("/ws")
    async def websocket(ws: WebSocket):
        await hub.serve(ws)

    # Laptop map page + MapLibre assets, served same-origin with the .pmtiles file.
    if routes_maps.WEB_DIR.is_dir():
        app.mount("/web", StaticFiles(directory=routes_maps.WEB_DIR), name="web")
    return app


# ---------------------------------------------------------------- network ---
def lan_addresses() -> list[str]:
    """Non-loopback IPv4 addresses of this machine, SAR-NET subnet first."""
    addrs = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            addrs.add(info[4][0])
    except socket.gaierror:
        pass
    try:  # the address of the default route, without sending anything
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.168.50.1", 9))
            addrs.add(s.getsockname()[0])
    except OSError:
        pass
    addrs = [a for a in addrs if not a.startswith("127.") and not a.startswith("169.254.")]
    return sorted(addrs, key=lambda a: (not a.startswith(PREFERRED_SUBNET), a))


def server_url(manifest) -> str:
    port = manifest.settings.get("server_port")
    host = manifest.settings.get("server_host_ip") or next(iter(lan_addresses()), "127.0.0.1")
    return f"http://{host}:{port}"


class ServerThread(threading.Thread):
    def __init__(self, manifest, host: str = "0.0.0.0", port: int | None = None, advertise: bool = True):
        super().__init__(name="sar-api", daemon=True)
        self.manifest = manifest
        self.port = port or manifest.settings.get("server_port")
        self.app = create_app(manifest)
        config = uvicorn.Config(self.app, host=host, port=self.port, log_level="warning", ws="websockets-sansio",
                                lifespan="on", access_log=False)
        self.server = uvicorn.Server(config)
        self.advertise = advertise
        self._zeroconf = None
        self.error: BaseException | None = None

    @property
    def hub(self) -> WsHub:
        return self.app.state.hub

    def run(self) -> None:
        try:
            self.server.run()
        except BaseException as exc:  # surfaced to the GUI via .error
            self.error = exc
            log.exception("API server stopped")

    def start_and_wait(self, timeout: float = 10.0) -> bool:
        self.start()
        waited = 0.0
        while waited < timeout and not self.server.started and self.is_alive():
            threading.Event().wait(0.05)
            waited += 0.05
        if self.server.started and self.advertise:
            self._advertise()
        return self.server.started

    def _advertise(self) -> None:
        try:
            from zeroconf import ServiceInfo, Zeroconf

            addrs = lan_addresses()
            if not addrs:
                return
            info = ServiceInfo(
                MDNS_TYPE, f"SAR Manifest {socket.gethostname()}.{MDNS_TYPE}",
                addresses=[socket.inet_aton(a) for a in addrs], port=self.port,
                properties={"version": VERSION, "path": "/api/v1"},
            )
            self._zeroconf = Zeroconf()
            self._zeroconf.register_service(info)
        except Exception:  # discovery is a fallback; the QR code still works
            log.warning("mDNS advertisement failed", exc_info=True)
            self._zeroconf = None

    def stop(self, timeout: float = 5.0) -> None:
        if self._zeroconf is not None:
            try:
                self._zeroconf.unregister_all_services()
                self._zeroconf.close()
            except Exception:
                pass
        self.server.should_exit = True
        self.join(timeout)
