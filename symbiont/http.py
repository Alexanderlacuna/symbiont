"""Every byte that leaves this machine goes through Fetcher, and every request is logged.

The run record is built from this log, so "requests" and "bytes" in the cost line are
measured, never estimated. A truncated body is not a smaller fetch: the Content-Length is
checked and a short read is retried, because a silently short file would poison the raw zone.
"""
from __future__ import annotations

import hashlib
import http.client
import socket
import time
import urllib.error
import urllib.request

from .contract import BudgetExceeded, SourceGone

UA = "app-farm-symbiont/1 (research layer; contact: owner)"
TIMEOUT = 90
ATTEMPTS = 3
GONE_STATUS = (404, 410)   # the provider has taken the file away: T9, not a transient failure


class _Redirect(urllib.request.HTTPRedirectHandler):
    """Python 3.10 does not follow 308, and ensembl.org answers 308 to its archive host — a
    symbiont that refuses 308 silently harvests nothing. Treat 308 as 307 (method preserved)."""

    http_error_308 = urllib.request.HTTPRedirectHandler.http_error_307

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return super().redirect_request(req, fp, 307 if code == 308 else code, msg, headers, newurl)


class FetchError(RuntimeError):
    pass


class HttpStatus(FetchError):
    """A non-200 answer. Carries the status so the caller can tell `gone` from `unreachable`."""

    def __init__(self, msg: str, status: int, url: str):
        super().__init__(msg)
        self.status = status
        self.url = url


RETRYABLE = (http.client.IncompleteRead, socket.timeout, ConnectionError, TimeoutError)


class Fetcher:
    """Every request is logged, and every request is CHARGED against the source's budget.

    budget     {requests, bytes, wall_s} from sources.yml — the cap is per source per run. The
               moment a request would cross it, BudgetExceeded is raised BEFORE the request leaves
               the box (T5), so a budget-capped source costs nothing beyond its cap.
    rate_limit {requests_per_minute, min_interval_s} from sources.yml — the wait is slept and then
               COUNTED (`waits`), because a rate limit that is obeyed silently cannot be audited.
    """

    def __init__(self, budget: dict | None = None, rate_limit: dict | None = None,
                 deadline_s: int = 900):
        self.log: list[dict] = []
        self.bytes_in = 0
        self.budget = dict(budget or {})
        self.rate = dict(rate_limit or {})
        self.waits: list[float] = []
        wall_cap = self.budget.get("wall_s")
        self.deadline_s = min(deadline_s, wall_cap) if wall_cap else deadline_s
        self._deadline = time.time() + self.deadline_s
        self._t0 = time.time()
        self._sent: list[float] = []
        self._opener = urllib.request.build_opener(_Redirect)

    # -- budget + rate -----------------------------------------------------
    def charge_request(self, url: str) -> None:
        cap = self.budget.get("requests")
        n = len(self.log) + 1
        if cap is not None and n > cap:
            raise BudgetExceeded(f"request #{n} to {url} would spend the source's budget of {cap} "
                                 f"request(s) for this run")

    def _charge_bytes(self, url: str, n: int) -> None:
        cap = self.budget.get("bytes")
        if cap is not None and self.bytes_in + n > cap:
            raise BudgetExceeded(f"{n} more byte(s) from {url} would cross the source's budget of "
                                 f"{cap} byte(s) (already in: {self.bytes_in})")

    def _charge_wall(self, url: str) -> None:
        cap = self.budget.get("wall_s")
        if cap is not None and time.time() - self._t0 > cap:
            raise BudgetExceeded(f"{url} would be requested after the source's budget of {cap}s wall "
                                 f"({time.time() - self._t0:.1f}s spent)")

    def _throttle(self) -> None:
        """min_interval_s between requests, and at most requests_per_minute in any 60 s window."""
        now = time.time()
        wait = 0.0
        min_i = float(self.rate.get("min_interval_s") or 0)
        if min_i and self._sent:
            wait = max(wait, self._sent[-1] + min_i - now)
        rpm = float(self.rate.get("requests_per_minute") or 0)
        if rpm:
            window = [t for t in self._sent if t > now - 60]
            if len(window) >= rpm:
                wait = max(wait, window[0] + 60 - now)
        if wait > 0:
            time.sleep(wait)
            self.waits.append(round(wait, 3))
        self._sent.append(time.time())

    @property
    def wall_seconds(self) -> float:
        return round(time.time() - self._t0, 1)

    # -- internals ---------------------------------------------------------
    def _req(self, url: str, method: str) -> urllib.request.Request:
        r = urllib.request.Request(url, method=method)
        r.add_header("User-Agent", UA)
        r.add_header("Accept", "*/*")
        return r

    def _once(self, url: str, method: str) -> tuple[int, dict, bytes, str]:
        with self._opener.open(self._req(url, method), timeout=TIMEOUT) as resp:
            body = resp.read() if method == "GET" else b""
            headers = {k.lower(): v for k, v in resp.headers.items()}
            return resp.status, headers, body, resp.geturl()

    def _do(self, url: str, method: str, note: str) -> tuple[int, dict, bytes]:
        last = None
        for attempt in range(1, ATTEMPTS + 1):
            if time.time() > self._deadline:
                raise FetchError(f"deadline exhausted before {url}")
            self.charge_request(url)
            self._charge_wall(url)
            self._throttle()
            n = len(self.log) + 1
            t0 = time.time()
            try:
                status, headers, body, final = self._once(url, method)
            except urllib.error.HTTPError as e:  # a 4xx/5xx is data too — log it, do not retry blindly
                body = e.read() if e.fp else b""
                self._charge_bytes(url, len(body))
                self.bytes_in += len(body)
                self.log.append(dict(n=n, method=method, url=url, effective_url=getattr(e, "url", url),
                                     status=e.code, bytes=len(body), ms=int((time.time() - t0) * 1000),
                                     sha256=hashlib.sha256(body).hexdigest() if body else "",
                                     note=f"{note} (http error, attempt {attempt})"))
                return e.code, {k.lower(): v for k, v in (e.headers or {}).items()}, body
            except RETRYABLE + (urllib.error.URLError,) as e:
                last = e
                self.log.append(dict(n=n, method=method, url=url, effective_url=url, status=0, bytes=0,
                                     ms=int((time.time() - t0) * 1000), sha256="",
                                     note=f"{note} (attempt {attempt}/{ATTEMPTS} died: {type(e).__name__})"))
                if attempt < ATTEMPTS:
                    time.sleep(2 * attempt)
                    continue
                raise FetchError(f"{method} {url} failed after {ATTEMPTS} attempts: "
                                 f"{type(e).__name__}: {e}") from e

            self._charge_bytes(url, len(body))
            self.bytes_in += len(body)
            expected = headers.get("content-length", "")
            if method == "GET" and expected.isdigit() and int(expected) != len(body):
                last = IncompleteRead(f"{len(body)} of {expected} bytes")
                self.log.append(dict(n=n, method=method, url=url, effective_url=final, status=status,
                                     bytes=len(body), ms=int((time.time() - t0) * 1000), sha256="",
                                     note=f"{note} (SHORT READ {len(body)}/{expected}, attempt {attempt})"))
                if attempt < ATTEMPTS:
                    time.sleep(2 * attempt)
                    continue
                raise FetchError(f"{method} {url}: short read after {ATTEMPTS} attempts "
                                 f"({len(body)} of {expected} bytes) — refusing a poisoned snapshot")
            self.log.append(dict(n=n, method=method, url=url, effective_url=final, status=status,
                                 bytes=len(body), ms=int((time.time() - t0) * 1000),
                                 sha256=hashlib.sha256(body).hexdigest() if body else "",
                                 note=f"{note} (attempt {attempt})" if attempt > 1 else note))
            return status, headers, body
        raise FetchError(f"{method} {url} exhausted retries: {last}")

    # -- public ------------------------------------------------------------
    def get(self, url: str, note: str = "data") -> bytes:
        status, _, body = self._do(url, "GET", note)
        if status != 200:
            if status in GONE_STATUS:
                raise SourceGone(f"GET {url} -> HTTP {status}: the provider no longer serves this resource")
            raise HttpStatus(f"GET {url} -> HTTP {status}", status, url)
        return body

    def get_text(self, url: str, note: str = "probe") -> str:
        return self.get(url, note).decode("utf-8", "replace")

    def head(self, url: str, note: str = "probe") -> dict:
        status, headers, _ = self._do(url, "HEAD", note)
        if status != 200:
            if status in GONE_STATUS:
                raise SourceGone(f"HEAD {url} -> HTTP {status}: the provider no longer serves this resource")
            raise HttpStatus(f"HEAD {url} -> HTTP {status}", status, url)
        return {
            "status": status,
            "etag": headers.get("etag", ""),
            "last_modified": headers.get("last-modified", ""),
            "content_length": int(headers["content-length"]) if headers.get("content-length", "").isdigit() else 0,
        }

    def last_effective_url(self) -> str:
        return self.log[-1].get("effective_url", "") if self.log else ""
