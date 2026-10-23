import json
import threading
import time
import urllib.error
import urllib.request

UA = "jobboard/0.1 (personal job search; links back to original postings)"


class Skip(Exception):
    """A source that cannot run right now (e.g. no API key). Reported as 'skipped', not as an error."""


class RateLimited(Exception):
    """HTTP 429 / 999 from a site: stop hitting it for now."""


def get_text(url: str, timeout: int = 25, headers: dict | None = None) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        if exc.code in (429, 999):
            raise RateLimited(f"HTTP {exc.code} from {url.split('/')[2]}") from exc
        raise


def get_json(url: str, timeout: int = 25, headers: dict | None = None):
    return json.loads(get_text(url, timeout, headers))


def post_json(url: str, body: dict, timeout: int = 40, headers: dict | None = None):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"User-Agent": UA, "Content-Type": "application/json", "Accept": "application/json",
                                          **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        if exc.code in (429, 999):
            raise RateLimited(f"HTTP {exc.code} from {url.split('/')[2]}") from exc
        raise


class Pacer:
    """Thread-safe minimum gap between requests (a polite global rate for one source)."""

    def __init__(self, gap: float, clock=time.monotonic, sleep=time.sleep):
        self.gap, self._clock, self._sleep, self._next = gap, clock, sleep, 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            now = self._clock()
            delay = max(0.0, self._next - now)
            self._next = max(now, self._next) + self.gap
        if delay:
            self._sleep(delay)
