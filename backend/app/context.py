"""Per-request ambient values.

Two things are needed all over the codebase but belong to no single layer: the
request id (error envelopes, log lines) and the client IP (audit rows). Passing
them down through every signature would put a ``Request`` parameter on routes
that have no other use for one, so the middleware in ``app.main`` stashes them
here and the consumers read them.

ContextVars are set per request and never shared between concurrent requests.
"""

import ipaddress
from contextvars import ContextVar

from fastapi import Request

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")
client_ip_var: ContextVar[str | None] = ContextVar("client_ip", default=None)


def current_request_id() -> str:
    """Request id of the in-flight request, or ``"-"`` outside one."""
    return request_id_var.get()


def current_client_ip() -> str | None:
    """Client IP of the in-flight request, or None if unknown/unparseable."""
    return client_ip_var.get()


def extract_client_ip(request: Request) -> str | None:
    """Best-effort client IP as a string a Postgres ``INET`` column will accept.

    Behind Cloud Run the direct peer is the load balancer, so the left-most
    ``X-Forwarded-For`` entry is the real caller. Anything that is not a valid
    IP literal — a spoofed header, or Starlette's ``TestClient`` reporting
    ``"testclient"`` — becomes None rather than a row the database would reject.
    """
    forwarded = request.headers.get("X-Forwarded-For", "")
    candidate = forwarded.split(",")[0].strip() or (request.client.host if request.client else "")
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        return None
