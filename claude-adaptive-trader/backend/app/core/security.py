"""Request guards for a local, single-user application.

The API listens on 127.0.0.1, but a browser on the same machine will still send requests to
it from any web page the user happens to visit. Three checks close that door:

* ``Host`` must be a loopback name, which defeats DNS rebinding.
* A cross-origin request must come from an allowed origin (CORS also enforces this for
  reading responses, but not for sending simple requests).
* Every state-changing request must carry the ``X-CAT-Client`` header. Browsers refuse to
  add a custom header cross-origin without a successful CORS preflight, so a hostile page
  cannot trigger an action even with a plain HTML form.
"""
from __future__ import annotations

from typing import Iterable
from urllib.parse import urlsplit

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

CLIENT_HEADER = "x-cat-client"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]", "testserver"}


class LocalGuardMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp, allowed_origins: Iterable[str], extra_hosts: Iterable[str] = ()) -> None:
        super().__init__(app)
        self.allowed_origins = {o.rstrip("/") for o in allowed_origins}
        self.hosts = LOOPBACK_HOSTS | set(extra_hosts)

    async def dispatch(self, request: Request, call_next) -> Response:  # type: ignore[override]
        host = (request.headers.get("host") or "").strip()
        hostname = urlsplit(f"//{host}").hostname or ""
        if hostname not in self.hosts and f"[{hostname}]" not in self.hosts:
            return JSONResponse({"error": "forbidden_host", "message": "unknown Host header"}, status_code=403)

        origin = request.headers.get("origin")
        if origin and origin.rstrip("/") not in self.allowed_origins and not _same_origin(origin, host):
            return JSONResponse({"error": "forbidden_origin", "message": "origin not allowed"}, status_code=403)

        if request.method not in SAFE_METHODS and request.url.path.startswith("/api/"):
            if request.headers.get(CLIENT_HEADER) is None:
                return JSONResponse(
                    {"error": "missing_client_header",
                     "message": f"state-changing requests must send the {CLIENT_HEADER} header"},
                    status_code=403)
        return await call_next(request)


def _same_origin(origin: str, host: str) -> bool:
    parts = urlsplit(origin)
    return parts.netloc == host
