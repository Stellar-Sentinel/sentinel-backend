"""Process-local sliding-window limits for public screening requests."""
from collections import defaultdict, deque
from ipaddress import ip_address, ip_network
from math import ceil
from threading import Lock
from time import monotonic


class ScreeningRateLimitMiddleware:
    """Limit POST /risk/score without trusting unconfigured proxy headers."""

    def __init__(self, app, requests: int, window_seconds: int, trusted_proxies):
        self.app = app
        self.requests = requests
        self.window_seconds = window_seconds
        self.trusted_proxies = tuple(trusted_proxies)
        self._hits = defaultdict(deque)
        self._lock = Lock()
        self._last_prune = monotonic()

    async def __call__(self, scope, receive, send):
        if (scope["type"] != "http" or scope["method"] != "POST"
                or scope["path"] != "/risk/score"):
            await self.app(scope, receive, send)
            return

        key = self._client_ip(scope)
        now = monotonic()
        with self._lock:
            if now - self._last_prune >= self.window_seconds:
                self._prune(now)
                self._last_prune = now
            hits = self._hits[key]
            cutoff = now - self.window_seconds
            while hits and hits[0] <= cutoff:
                hits.popleft()
            if len(hits) >= self.requests:
                retry_after = max(1, ceil(hits[0] + self.window_seconds - now))
                remaining = 0
            else:
                hits.append(now)
                retry_after = 0
                remaining = self.requests - len(hits)

        if retry_after:
            body = b'{"detail":"Screening rate limit exceeded. Try again later."}'
            headers = [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
                (b"retry-after", str(retry_after).encode("ascii")),
                (b"x-ratelimit-limit", str(self.requests).encode("ascii")),
                (b"x-ratelimit-remaining", b"0"),
            ]
            await send({"type": "http.response.start", "status": 429, "headers": headers})
            await send({"type": "http.response.body", "body": body})
            return

        async def send_with_rate_headers(message):
            if message["type"] == "http.response.start":
                message["headers"].extend([
                    (b"x-ratelimit-limit", str(self.requests).encode("ascii")),
                    (b"x-ratelimit-remaining", str(remaining).encode("ascii")),
                ])
            await send(message)

        await self.app(scope, receive, send_with_rate_headers)

    def _client_ip(self, scope):
        peer = scope.get("client")
        try:
            remote = ip_address(peer[0]) if peer else None
        except ValueError:
            remote = None

        if remote is None or not self._is_trusted(remote):
            return str(remote) if remote is not None else "unknown"

        headers = dict(scope.get("headers", []))
        forwarded = headers.get(b"x-forwarded-for", b"").decode("ascii", errors="ignore")
        chain = []
        for item in forwarded.split(","):
            try:
                chain.append(ip_address(item.strip()))
            except ValueError:
                return str(remote)

        # Walk from the trusted proxy toward the original client. Stop at the
        # first untrusted hop so a client-supplied leftmost value is ignored.
        for address in reversed(chain):
            if not self._is_trusted(address):
                return str(address)
        return str(chain[0]) if chain else str(remote)

    def _is_trusted(self, address):
        return any(address in network for network in self.trusted_proxies)

    def _prune(self, now):
        cutoff = now - self.window_seconds
        for client, hits in list(self._hits.items()):
            while hits and hits[0] <= cutoff:
                hits.popleft()
            if not hits:
                self._hits.pop(client, None)
