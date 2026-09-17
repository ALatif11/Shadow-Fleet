"""HTTP helpers: polite client, retrying GET, resumable streaming download with size check."""

from __future__ import annotations

import logging
import time
from pathlib import Path

import httpx

log = logging.getLogger(__name__)
USER_AGENT = "shadowfleet/0.0 (non-commercial research portfolio; github.com/ALatif11)"
RETRY_STATUS = {429, 500, 502, 503, 504}


def client(timeout: float = 60.0, **kw) -> httpx.Client:
    headers = {"User-Agent": USER_AGENT, **kw.pop("headers", {})}
    return httpx.Client(timeout=timeout, follow_redirects=True, headers=headers, **kw)


def backoff_s(attempt: int, retry_after: str | None = None, cap: float = 300.0) -> float:
    if retry_after:
        try:
            return min(float(retry_after), cap)
        except ValueError:
            pass
    return min(2.0 ** attempt, cap)


def get(c: httpx.Client, url: str, retries: int = 4, _sleep=time.sleep, **kw) -> httpx.Response:
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            r = c.get(url, **kw)
            if r.status_code in RETRY_STATUS and attempt < retries:
                wait = backoff_s(attempt, r.headers.get("Retry-After"))
                log.warning("retrying", extra={"url": url, "status": r.status_code, "wait_s": wait})
                _sleep(wait)
                continue
            return r
        except (httpx.TransportError, httpx.TimeoutException) as e:
            last = e
            if attempt >= retries:
                raise
            wait = backoff_s(attempt)
            log.warning("retrying after error", extra={"url": url, "err": repr(e), "wait_s": wait})
            _sleep(wait)
    raise RuntimeError(f"unreachable: {url} {last!r}")


def first_ok(c: httpx.Client, urls: list[str], **kw) -> tuple[str, httpx.Response]:
    """Try candidate URLs in order; return the first 200. Raises with every status seen."""
    seen = []
    for u in urls:
        try:
            r = get(c, u, retries=2, **kw)
        except httpx.HTTPError as e:
            seen.append(f"{u}: {e!r}")
            continue
        if r.status_code == 200:
            return u, r
        seen.append(f"{u}: HTTP {r.status_code}")
    raise RuntimeError("no candidate URL answered: " + "; ".join(seen))


def head_size(c: httpx.Client, url: str) -> int | None:
    try:
        r = c.head(url)
    except httpx.HTTPError:
        return None
    if r.status_code != 200:
        return None
    n = r.headers.get("Content-Length")
    return int(n) if n and n.isdigit() else None


def download(
    c: httpx.Client, url: str, dest: Path, retries: int = 3, chunk: int = 1 << 20, _sleep=time.sleep
) -> int:
    """Stream `url` to `dest` via `dest.part`, resuming with Range when possible.

    Verifies the final size against Content-Length when the server sends one. Returns bytes written.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    for attempt in range(retries + 1):
        have = part.stat().st_size if part.exists() else 0
        headers = {"Range": f"bytes={have}-"} if have else {}
        try:
            with c.stream("GET", url, headers=headers) as r:
                if r.status_code == 416:  # already complete
                    break
                if r.status_code not in (200, 206):
                    if r.status_code in RETRY_STATUS and attempt < retries:
                        _sleep(backoff_s(attempt, r.headers.get("Retry-After")))
                        continue
                    raise httpx.HTTPStatusError(f"HTTP {r.status_code}", request=r.request, response=r)
                if r.status_code == 200 and have:
                    have = 0  # server ignored Range; start over
                total = r.headers.get("Content-Length")
                expected = (int(total) + have) if total and total.isdigit() else None
                with open(part, "ab" if have else "wb") as f:
                    for block in r.iter_bytes(chunk):
                        f.write(block)
            size = part.stat().st_size
            if expected is not None and size != expected:
                raise OSError(f"size mismatch for {url}: got {size}, expected {expected}")
            part.replace(dest)
            return size
        except (httpx.TransportError, httpx.TimeoutException, OSError) as e:
            if attempt >= retries:
                raise
            wait = backoff_s(attempt)
            log.warning("download retry", extra={"url": url, "err": repr(e), "wait_s": wait})
            _sleep(wait)
    part.replace(dest)
    return dest.stat().st_size
