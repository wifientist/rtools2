"""
The client's real IP, for an app that is only ever reached through nginx.

Behind rtools-nginx, request.client.host is NGINX's container address for
every user. Anything keyed on it treated all users as one client: the rate
limiter gave everyone a single shared bucket per endpoint -- so the 5-per-
minute login OTP limit was 5 per minute for the whole user base -- and
audit records logged the proxy's address for every action.

nginx forwards the address it saw in X-Real-IP. That header is only
trustworthy when nginx is the one sending it: the backend's port 4174 is
also published directly, and a client connecting there could set any
X-Real-IP it liked, picking a fresh rate-limit bucket for every request.
So the header is honoured only when the direct peer is a trusted proxy.

TRUSTED_PROXIES: comma-separated hostnames or IPs, default "nginx" -- the
compose service name, resolved through Docker's embedded DNS. Resolved
lazily and refreshed every minute, because a --force-recreate can hand
nginx a new address.

If something else terminates TLS in front of rtools-nginx, nginx's own
$remote_addr is that hop, not the user, and nginx needs set_real_ip_from
for it before this can see real clients.
"""

import ipaddress
import logging
import os
import socket
import time
from typing import FrozenSet, Optional

from starlette.requests import Request

logger = logging.getLogger(__name__)

_REFRESH_SECONDS = 60.0
_cache = {"at": float("-inf"), "ips": frozenset()}


def _trusted_proxy_ips() -> FrozenSet[str]:
    """Addresses of the proxies allowed to vouch for a client's IP."""
    now = time.monotonic()
    if now - _cache["at"] < _REFRESH_SECONDS:
        return _cache["ips"]

    ips = set()
    for name in filter(None, (h.strip() for h in
                              os.getenv("TRUSTED_PROXIES", "nginx").split(","))):
        try:
            # Docker's DNS answers service names locally, so this is cheap --
            # and it runs at most once a minute, not per request.
            for info in socket.getaddrinfo(name, None):
                ips.add(info[4][0])
        except OSError as e:
            logger.debug(f"Trusted proxy '{name}' did not resolve: {e}")

    _cache["at"], _cache["ips"] = now, frozenset(ips)
    return _cache["ips"]


def client_ip(request: Request) -> Optional[str]:
    """
    The originating client's IP.

    X-Real-IP when the request came through a trusted proxy and the header
    holds a valid address; otherwise the direct peer, which is the only
    thing an untrusted connection cannot lie about.
    """
    peer = request.client.host if request.client else None
    if peer and peer in _trusted_proxy_ips():
        forwarded = (request.headers.get("x-real-ip") or "").strip()
        try:
            return str(ipaddress.ip_address(forwarded))
        except ValueError:
            pass  # absent or junk: fall back to the peer rather than trust it
    return peer
