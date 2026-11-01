"""A polite HTTP client: identifies itself, paces requests, retries, caches JSON.

Wikimedia asks every client to send a descriptive User-Agent with contact
details and to back off when told to (HTTP 429 with ``Retry-After``). This
client does both, and caches JSON responses on disk so that re-running a step
does not hit the APIs again.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
import warnings
from pathlib import Path
from urllib.parse import urlencode, urlparse

import requests

from .. import __version__

log = logging.getLogger(__name__)

RETRY_STATUSES = {429, 500, 502, 503, 504}

# Seconds between requests. The MediaWiki APIs of Wikipedia, Wikidata and Commons
# share one per-IP budget, so they share one pacing slot ("wikimedia-api").
# Faster than ~1 request every 3 s from a cloud address triggers HTTP 429.
DEFAULT_INTERVALS = {
    "wikimedia-api": 3.0,
    "upload.wikimedia.org": 1.5,
    "query.wikidata.org": 1.0,
}


def pace_key(host: str) -> str:
    if host == "query.wikidata.org" or host == "upload.wikimedia.org":
        return host
    if host.endswith(("wikipedia.org", "wikidata.org", "wikimedia.org")):
        return "wikimedia-api"
    return host


class HttpError(RuntimeError):
    pass


class HttpClient:
    """Rate-limited, retrying, caching HTTP client shared by all source modules."""

    def __init__(
        self,
        *,
        contact: str = "",
        cache_dir: str | Path | None = None,
        min_interval: float = 1.0,
        timeout: float = 60.0,
        max_retries: int = 6,
        max_wait: float = 180.0,
        session: requests.Session | None = None,
        intervals: dict[str, float] | None = None,
    ) -> None:
        self.contact = contact.strip()
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.min_interval = min_interval
        self.intervals = {**DEFAULT_INTERVALS, **(intervals or {})}
        self.timeout = timeout
        self.max_retries = max_retries
        self.max_wait = max_wait
        self.session = session or requests.Session()
        self.session.headers["User-Agent"] = self.user_agent
        self._last: dict[str, float] = {}
        self._warned_contact = False
        self.requests_made = 0

    @property
    def user_agent(self) -> str:
        contact = self.contact or "contact not configured"
        return (
            f"gameheritage/{__version__} (video game digital heritage research dataset; "
            f"{contact}) python-requests/{requests.__version__}"
        )

    def _pace(self, host: str) -> None:
        key = pace_key(host)
        interval = max(self.intervals.get(key, 0.0), self.min_interval)
        wait = self._last.get(key, 0.0) + interval - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last[key] = time.monotonic()

    def _cache_path(self, url: str, params: dict | None) -> Path | None:
        if not self.cache_dir:
            return None
        key = url + "?" + urlencode(sorted((params or {}).items()), doseq=True)
        digest = hashlib.sha256(key.encode()).hexdigest()
        return self.cache_dir / "json" / digest[:2] / f"{digest}.json"

    def request(self, method: str, url: str, **kwargs) -> requests.Response:
        host = urlparse(url).netloc
        wikimedia = host.endswith(("wikimedia.org", "wikipedia.org", "wikidata.org"))
        if wikimedia and not self.contact and not self._warned_contact:
            warnings.warn(
                "Wikimedia asks API clients to include contact details in the User-Agent. "
                "Set [http] contact in gameheritage.toml or GAMEHERITAGE_CONTACT.",
                stacklevel=3,
            )
            self._warned_contact = True
        delay = 2.0
        for attempt in range(self.max_retries + 1):
            self._pace(host)
            try:
                resp = self.session.request(method, url, timeout=self.timeout, **kwargs)
                self.requests_made += 1
            except requests.RequestException as exc:
                if "Tunnel connection failed: 403" in str(exc) or attempt >= self.max_retries:
                    # A proxy policy refusal will not change on retry.
                    raise HttpError(f"{method} {url} failed: {exc}") from exc
                log.warning("network error on %s (%s); retrying in %.0fs", url, exc, delay)
                time.sleep(delay)
                delay = min(delay * 2, self.max_wait)
                continue
            if resp.status_code in RETRY_STATUSES and attempt < self.max_retries:
                ra = resp.headers.get("Retry-After", "")
                wait = float(ra) if ra.replace(".", "", 1).isdigit() else delay
                if wait > 3 * self.max_wait:
                    # Asked to stay away for many minutes (e.g. 600 s for original
                    # files): give up on this request instead of stalling the run.
                    raise HttpError(
                        f"{method} {url} -> HTTP {resp.status_code}; retry after {wait:.0f}s"
                    )
                wait = min(max(wait, 1.0), self.max_wait)
                log.info("HTTP %s from %s; waiting %.0fs", resp.status_code, host, wait)
                time.sleep(wait)
                delay = min(delay * 2, self.max_wait)
                continue
            if resp.status_code >= 400:
                raise HttpError(f"{method} {url} -> HTTP {resp.status_code}: {resp.text[:200]}")
            return resp
        raise HttpError(f"{method} {url}: retries exhausted")  # pragma: no cover

    def get_json(
        self, url: str, params: dict | None = None, *, refresh: bool = False, **kwargs
    ) -> dict:
        """GET a JSON document, served from the disk cache when available."""
        path = self._cache_path(url, params)
        if path and path.exists() and not refresh:
            cached = json.loads(path.read_text("utf-8"))
            if not (isinstance(cached, dict) and "error" in cached):
                return cached
        for attempt in range(self.max_retries + 1):
            resp = self.request("GET", url, params=params, **kwargs)
            data = resp.json()
            err = data.get("error") if isinstance(data, dict) else None
            if not err:
                break
            # MediaWiki reports server lag and some refusals as HTTP 200 + "error".
            if err.get("code") == "maxlag" and attempt < self.max_retries:
                time.sleep(min(5.0 * (attempt + 1), self.max_wait))
                continue
            raise HttpError(f"GET {url}: API error {err.get('code')}: {err.get('info', '')[:200]}")
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data), "utf-8")
        return data

    def download(self, url: str, dest: str | Path) -> tuple[Path, str, int]:
        """Stream a file to ``dest``. Returns (path, sha256, bytes)."""
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        resp = self.request("GET", url, stream=True)
        h = hashlib.sha256()
        n = 0
        tmp = dest.with_suffix(dest.suffix + ".part")
        with open(tmp, "wb") as f:
            for chunk in resp.iter_content(1 << 16):
                f.write(chunk)
                h.update(chunk)
                n += len(chunk)
        tmp.replace(dest)
        return dest, h.hexdigest(), n


PROJECT_URL = "https://github.com/cantayus/gameheritage"


def public_client() -> HttpClient:
    """Client for researchers' downloads: project URL as contact, polite pacing for
    Wikimedia and Fandom, none for the project's own GitHub-hosted files."""
    import os

    return HttpClient(
        contact=os.environ.get("GAMEHERITAGE_CONTACT") or PROJECT_URL,
        min_interval=0.0,
        intervals={"static.wikia.nocookie.net": 0.5},
    )
