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
import urllib.parse
import urllib.request

from .contract import BudgetExceeded, Refusal, SourceGone

UA = "app-farm-symbiont/1 (research layer; contact: owner)"
TIMEOUT = 90
ATTEMPTS = 3
GONE_STATUS = (404, 410)   # the provider has taken the file away: T9, not a transient failure
REDIRECT_STATUS = (301, 302, 303, 307, 308)


class _Redirect(urllib.request.HTTPRedirectHandler):
    """Python 3.10 does not follow 308, and ensembl.org answers 308 to its archive host — a
    symbiont that refuses 308 silently harvests nothing. Treat 308 as 307 (method preserved)."""

    http_error_308 = urllib.request.HTTPRedirectHandler.http_error_307

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return super().redirect_request(req, fp, 307 if code == 308 else code, msg, headers, newurl)


# ── REDIRECTS ARE PINNED AT ADMISSION, AND A HOP OFF THE PINNED SET IS REFUSED ──────────────
# req-P1-ALLOWLIST-IS-DECLARED-ENDPOINTS-RED (the CLASS of req-P1-THE-LICENCE-HOST-IS-IN-THE-ALLOWL).
# MEASURED 2026-11-20 on the live MONDO case (reports/planning/evidence/…/mondo_live_20261120T1437):
# the licence page was READ, and then the data fetch died on the 302 the release asset makes —
#   REFUSED[sandbox-breach] network: release-assets.githubusercontent.com — host not in the
#   adapter's network allowlist
# A redirect FOLLOWED at run time widens an allowlist by execution: the payload decides, one hop
# at a time, which hosts are reachable, and nobody declared any of them. So the chain is resolved
# ONCE, before the payload exists — symbiont/sandbox.py: pinned_declarations() follows it hop by
# hop (chain(), below), pins the hosts a DECLARATION justifies, and prints each one with its
# reason. What remains at run time is this class: a hop whose target host nobody pinned is
# REFUSED by name, both hosts of the hop quoted, never followed.
class RedirectUndeclared(Refusal):
    """REFUSED[redirect-undeclared] — a redirect points at a host nobody pinned at admission.

    Carries `from_host` and `to_host`, so the refusal NAMES BOTH ENDS OF THE HOP: the operator
    sees which host redirected and which host it wanted, never just 'network refused'."""
    code = "redirect-undeclared"

    def __init__(self, from_host: str, to_host: str, url: str = "", pinned=()):
        detail = (f"{from_host or '(no host)'} -> {to_host or '(no host)'} — "
                  f"{url or 'this redirect'} points at a host no declaration pinned at admission "
                  f"(pinned here: {', '.join(sorted(pinned)) or 'nothing'}); a redirect is "
                  f"followed only to a host resolved at admission, never to one discovered by "
                  f"the payload's own execution")
        super().__init__(detail)
        self.from_host = from_host or ""
        self.to_host = to_host or ""
        self.url = url or ""
        self.pinned = sorted(pinned)


class _PinnedRedirect(_Redirect):
    """`_Redirect`, with the admission-pinned host set armed: a hop to any other host is
    REFUSED before the request leaves the box (the Location is inspected, never fetched)."""

    def __init__(self, pinned=()):
        super().__init__()
        self.pinned = {str(h).lower() for h in pinned if h}

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        frm = _host_of(getattr(req, "full_url", "") or "")
        to = _host_of(newurl)
        if to and self.pinned and to not in self.pinned:
            raise RedirectUndeclared(frm, to, newurl, self.pinned)
        return super().redirect_request(req, fp, 307 if code == 308 else code, msg, headers, newurl)


class _NoFollow(urllib.request.HTTPRedirectHandler):
    """Refuses to follow, so `chain()` can walk the hops ONE AT A TIME and name each of them
    before the next request is made. Following first and inspecting later is the hole: by then
    the bytes have already left the box for a host nobody declared."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None                      # -> urllib raises HTTPError, which carries Location


def chain(url: str, *, method: str = "HEAD", max_hops: int = 5,
          timeout: int = 20) -> list[dict]:
    """The redirect chain of `url`, resolved WITHOUT following it, one hop per entry:
    [{n, url, host, status, location, to_url, to_host}] (status 0 = unreachable, `error` set).

    THE MECHANISM ONLY: this function says what the chain IS. Which hosts may be PINNED — and
    which hop is REFUSED — is policy and lives in symbiont/sandbox.py (pinned_declarations), so
    no module that only speaks HTTP can widen an allowlist by itself."""
    hops: list[dict] = []
    opener = urllib.request.build_opener(_NoFollow)
    cur = url
    for i in range(max_hops + 1):
        req = urllib.request.Request(cur, method=method)
        req.add_header("User-Agent", UA)
        req.add_header("Accept", "*/*")
        status, headers = 0, {}
        try:
            with opener.open(req, timeout=timeout) as resp:
                status = resp.status
                headers = {k.lower(): v for k, v in resp.headers.items()}
        except urllib.error.HTTPError as e:      # a 3xx arrives here: _NoFollow never follows
            status = e.code
            headers = {k.lower(): v for k, v in (e.headers or {}).items()}
        except (urllib.error.URLError, OSError, http.client.HTTPException) as e:
            hops.append(dict(n=i + 1, url=cur, host=_host_of(cur), status=0, location="",
                             to_url="", to_host="", error=f"{type(e).__name__}: {e}"))
            break
        loc = headers.get("location", "")
        to_url = urllib.parse.urljoin(cur, loc) if loc else ""
        hops.append(dict(n=i + 1, url=cur, host=_host_of(cur), status=status, location=loc,
                         to_url=to_url, to_host=_host_of(to_url)))
        if status in REDIRECT_STATUS and to_url:
            cur = to_url
            continue
        break
    return hops


class FetchError(RuntimeError):
    pass


class HttpStatus(FetchError):
    """A non-200 answer. Carries the status so the caller can tell `gone` from `unreachable`."""

    def __init__(self, msg: str, status: int, url: str):
        super().__init__(msg)
        self.status = status
        self.url = url


RETRYABLE = (http.client.IncompleteRead, socket.timeout, ConnectionError, TimeoutError)


def _budget_refusal(detail: str, by: str) -> BudgetExceeded:
    """A budget refusal that NAMES ITS LAYER (req-P2-GRANTS-AND-INHERITANCE-the-stati).

    symbiont/policy.py's `meet(station, expedition)` hands this class the effective SPEND budget
    together with the OWNER of each counter, so the operator is told whose number was spent -
    `REFUSED[budget by=station]` means argue with the station, `by=expedition` means edit the
    route. A refusal that does not say which layer refused is the defect this pins shut.
    """
    e = BudgetExceeded(f"REFUSED[budget by={by}] {detail}")
    e.by = by
    return e


def _host_of(url: str) -> str:
    try:
        return (urllib.parse.urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


class Fetcher:
    """Every request is logged, and every request is CHARGED against the source's budget.

    budget     {requests, bytes, wall_s} from sources.yml — the cap is per source per run. The
               moment a request would cross it, BudgetExceeded is raised BEFORE the request leaves
               the box (T5), so a budget-capped source costs nothing beyond its cap.
    rate_limit {requests_per_minute, min_interval_s} from sources.yml — the wait is slept and then
               COUNTED (`waits`), because a rate limit that is obeyed silently cannot be audited.

    by         WHICH LAYER owns the budget — `station` or `expedition` (symbiont/policy.py). Every
               budget refusal reads `REFUSED[budget by=<by>] <detail>`, and the exception carries
               `.by`, so a run record can say who to argue with.
    spend_by   per-counter owners ({requests|bytes|wall_s|requests@host|bytes@host: layer}) when the
               effective budget is minimised from more than one layer: the counter that refused is
               the counter whose OWNER is named, never a blanket answer.
    per_host   {host: {requests, bytes}} — the per-host half of the effective SPEND grant. Charged
               the same way and at the same moment as the global counters: BEFORE bytes leave.
    pinned_hosts {host: reason} — the allowlist PINNED at admission (symbiont/sandbox.py:
               pinned_declarations). Every log entry carries it, so the run record says WHICH hosts
               were reachable and WHY. When it is not given, the fetcher reads the allowlist armed
               for it right now (sandbox.active_pinned()), so admission's guard is the one
               authority and a second copy cannot disagree with it.
    """

    def __init__(self, budget: dict | None = None, rate_limit: dict | None = None,
                 deadline_s: int = 900, by: str = "station", spend_by: dict | None = None,
                 per_host: dict | None = None, pinned_hosts: dict | None = None):
        self.log: list[dict] = []
        self.bytes_in = 0
        self.budget = dict(budget or {})
        self.rate = dict(rate_limit or {})
        self.by = by or "station"
        self.spend_by = dict(spend_by or {})
        self.per_host = {h: dict(c) for h, c in (per_host or {}).items()}
        self.requests_by_host: dict[str, int] = {}
        self.bytes_by_host: dict[str, int] = {}
        self.waits: list[float] = []
        wall_cap = self.budget.get("wall_s")
        self.deadline_s = min(deadline_s, wall_cap) if wall_cap else deadline_s
        self._deadline = time.time() + self.deadline_s
        self._t0 = time.time()
        self._sent: list[float] = []
        self.pinned = dict(pinned_hosts or {})
        self._opener = None      # built on the FIRST request — see _open()

    # -- the pinned allowlist ----------------------------------------------
    def _pinned_map(self) -> dict:
        """{host: reason} of the hosts this fetch may reach: the ones given, else the ones armed
        right now. Never a host discovered by the payload: the set is fixed before it runs."""
        if self.pinned:
            return dict(self.pinned)
        try:
            from . import sandbox
            return sandbox.active_pinned()
        except Exception:
            return {}

    def _open(self):
        """The opener, built LAZILY: admission arms sandbox.network_guard() around the harvest,
        and the fetcher is constructed before that block opens — resolving the pins at __init__
        would pin the empty set and quietly follow every hop."""
        if self._opener is None:
            pins = self._pinned_map()
            self._opener = urllib.request.build_opener(_PinnedRedirect(pins) if pins else _Redirect())
        return self._opener

    # -- budget + rate -----------------------------------------------------
    def _owner(self, key: str, host: str = "") -> str:
        """The layer that OWNS a counter: its per-host owner, then its global owner, then `by`."""
        if host:
            got = self.spend_by.get(f"{key}@{host}")
            if got:
                return got
        return self.spend_by.get(key) or self.by

    def charge_request(self, url: str) -> None:
        cap = self.budget.get("requests")
        n = len(self.log) + 1
        if cap is not None and n > cap:
            raise _budget_refusal(
                f"request #{n} to {url} would spend the budget of {cap} request(s) for this run",
                self._owner("requests"))
        host = _host_of(url)
        hcap = (self.per_host.get(host) or {}).get("requests")
        hn = self.requests_by_host.get(host, 0) + 1
        if hcap is not None and hn > hcap:
            raise _budget_refusal(
                f"request #{hn} to {host} ({url}) would spend the per-host budget of {hcap} "
                f"request(s) for this run", self._owner("requests", host))
        self.requests_by_host[host] = hn        # CHARGED: the request is about to leave

    def _commit_bytes(self, url: str, n: int) -> None:
        """Book bytes that have actually landed, globally AND per host (the charges above are the
        gate; this is the ledger, and the two must name the same counters)."""
        self.bytes_in += n
        host = _host_of(url)
        if host:
            self.bytes_by_host[host] = self.bytes_by_host.get(host, 0) + n

    def _charge_bytes(self, url: str, n: int) -> None:
        cap = self.budget.get("bytes")
        if cap is not None and self.bytes_in + n > cap:
            raise _budget_refusal(
                f"{n} more byte(s) from {url} would cross the budget of {cap} byte(s) "
                f"(already in: {self.bytes_in})", self._owner("bytes"))
        host = _host_of(url)
        hcap = (self.per_host.get(host) or {}).get("bytes")
        hgot = self.bytes_by_host.get(host, 0)
        if hcap is not None and hgot + n > hcap:
            raise _budget_refusal(
                f"{n} more byte(s) from {host} ({url}) would cross the per-host budget of {hcap} "
                f"byte(s) (already in: {hgot})", self._owner("bytes", host))

    def _charge_wall(self, url: str) -> None:
        cap = self.budget.get("wall_s")
        if cap is not None and time.time() - self._t0 > cap:
            raise _budget_refusal(
                f"{url} would be requested after the budget of {cap}s wall "
                f"({time.time() - self._t0:.1f}s spent)", self._owner("wall_s"))

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
        with self._open().open(self._req(url, method), timeout=TIMEOUT) as resp:
            body = resp.read() if method == "GET" else b""
            headers = {k.lower(): v for k, v in resp.headers.items()}
            return resp.status, headers, body, resp.geturl()

    def _do(self, url: str, method: str, note: str) -> tuple[int, dict, bytes]:
        last = None
        # THE PINNED ALLOWLIST IS READ ONCE PER FETCH and written into every entry below, so the
        # run record says, fetch by fetch, WHICH hosts were reachable and WHY (deliverable 4).
        pins = self._pinned_map()
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
                self._commit_bytes(url, len(body))
                self.log.append(dict(n=n, method=method, url=url, effective_url=getattr(e, "url", url),
                                     status=e.code, bytes=len(body), ms=int((time.time() - t0) * 1000),
                                     sha256=hashlib.sha256(body).hexdigest() if body else "",
                                     pinned=pins,
                                     note=f"{note} (http error, attempt {attempt})"))
                return e.code, {k.lower(): v for k, v in (e.headers or {}).items()}, body
            except RETRYABLE + (urllib.error.URLError,) as e:
                last = e
                self.log.append(dict(n=n, method=method, url=url, effective_url=url, status=0, bytes=0,
                                     ms=int((time.time() - t0) * 1000), sha256="", pinned=pins,
                                     note=f"{note} (attempt {attempt}/{ATTEMPTS} died: {type(e).__name__})"))
                if attempt < ATTEMPTS:
                    time.sleep(2 * attempt)
                    continue
                raise FetchError(f"{method} {url} failed after {ATTEMPTS} attempts: "
                                 f"{type(e).__name__}: {e}") from e

            self._charge_bytes(url, len(body))
            self._commit_bytes(url, len(body))
            expected = headers.get("content-length", "")
            if method == "GET" and expected.isdigit() and int(expected) != len(body):
                last = IncompleteRead(f"{len(body)} of {expected} bytes")
                self.log.append(dict(n=n, method=method, url=url, effective_url=final, status=status,
                                     bytes=len(body), ms=int((time.time() - t0) * 1000), sha256="",
                                     pinned=pins,
                                     note=f"{note} (SHORT READ {len(body)}/{expected}, attempt {attempt})"))
                if attempt < ATTEMPTS:
                    time.sleep(2 * attempt)
                    continue
                raise FetchError(f"{method} {url}: short read after {ATTEMPTS} attempts "
                                 f"({len(body)} of {expected} bytes) — refusing a poisoned snapshot")
            self.log.append(dict(n=n, method=method, url=url, effective_url=final, status=status,
                                 bytes=len(body), ms=int((time.time() - t0) * 1000),
                                 sha256=hashlib.sha256(body).hexdigest() if body else "",
                                 pinned=pins,
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
