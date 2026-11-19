"""ERI Login application firewall for Render Free; no database or persistent state changed."""
from collections import deque
import ipaddress
import os
import threading
import time
from urllib.parse import urlsplit


def _cidrs(raw):
    networks = []
    for item in raw.replace(";", ",").split(","):
        item = item.strip()
        if item:
            networks.append(ipaddress.ip_network(item, strict=False))
    return tuple(networks)


def _integer(raw, default, lower, upper):
    try:
        return min(upper, max(lower, int(raw)))
    except (TypeError, ValueError):
        return default


class ERIFirewall:
    """Best-effort, process-local rate limiting + optional IP ACL.

    Authorization still occurs in the ERI API/backend. Never use Origin,
    User-Agent or a bundled iOS secret as client authentication.
    """

    def __init__(self, environ=None, clock=None):
        env = os.environ if environ is None else environ
        self.enabled = env.get("ERI_FIREWALL_ENABLED", "1") != "0"
        self.denied = _cidrs(env.get("ERI_FIREWALL_DENY_CIDRS", ""))
        self.allowed = _cidrs(env.get("ERI_FIREWALL_ALLOW_CIDRS", ""))
        self.trust_render_proxy = (env.get("ERI_FIREWALL_TRUST_RENDER_PROXY", "0") == "1")
        self.limits = {
            "public": _integer(env.get("ERI_FIREWALL_PUBLIC_PER_MIN"), 180, 10, 10000),
            "verify": _integer(env.get("ERI_FIREWALL_VERIFY_PER_MIN"), 120, 10, 10000),
            "admin": _integer(env.get("ERI_FIREWALL_ADMIN_PER_MIN"), 120, 10, 10000),
        }
        self.clock = clock or time.monotonic
        self.lock = threading.Lock()
        self.hits = {}

    def client_ip(self, peer_ip, headers):
        """Use rightmost XFF entry *only* when Render's trusted edge is configured.

        Render appends the actual client IP to X-Forwarded-For. Never enable
        this option when the backend is reachable through an untrusted proxy.
        """
        value = peer_ip
        if self.trust_render_proxy:
            forwarded = headers.get("X-Forwarded-For", "")
            if forwarded:
                value = forwarded.split(",")[-1].strip()
        try:
            return ipaddress.ip_address(value)
        except ValueError:
            try:
                return ipaddress.ip_address(peer_ip)
            except ValueError:
                return None

    def check(self, method, target, peer_ip, headers):
        """Return (HTTP status, error code, Retry-After seconds) or None."""
        if not self.enabled:
            return None
        if len(target) > 2048:
            return (414, "URI_TOO_LONG", None)
        client = self.client_ip(peer_ip, headers)
        if client is None:
            return (400, "INVALID_CLIENT_ADDRESS", None)
        if any(client in cidr for cidr in self.denied):
            return (403, "FIREWALL_DENIED", None)
        if self.allowed and not any(client in cidr for cidr in self.allowed):
            return (403, "FIREWALL_DENIED", None)
        path = urlsplit(target).path
        # Keep Render health probes unthrottled.
        if path in ("/", "/api/health") or method == "HEAD":
            return None
        group = ("admin" if path.startswith("/api/admin/")
                 else "verify" if path in ("/api/login", "/api/verify", "/api/key/activate", "/api/key/verify")
                 else "public")
        limit = self.limits[group]
        now = self.clock()
        key = (str(client), group)
        with self.lock:
            bucket = self.hits.setdefault(key, deque())
            while bucket and now - bucket[0] >= 60:
                bucket.popleft()
            if len(bucket) >= limit:
                retry = max(1, min(60, int(61 - (now - bucket[0]))))
                return (429, "RATE_LIMITED", retry)
            bucket.append(now)
            # Avoid unbounded RAM use if botnets spray new addresses.
            if len(self.hits) > 20000:
                self.hits = {
                    k: v for k, v in self.hits.items()
                    if v and now - v[-1] < 60
                }
                if len(self.hits) > 20000:
                    self.hits.clear()
        return None
