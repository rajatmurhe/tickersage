"""HTTP fetching with a per-attempt timeout, bounded retries and exponential backoff."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from .tools.errors import ToolError

log = logging.getLogger(__name__)

RETRY_STATUS = {408, 425, 429, 500, 502, 503, 504}


async def fetch(
    client: httpx.AsyncClient,
    url: str,
    params: dict[str, Any] | None = None,
    *,
    timeout: float = 5.0,
    retries: int = 2,
    backoff: float = 0.25,
    as_text: bool = False,
) -> Any:
    """GET `url`. Retries timeouts, transport errors and 429/5xx; never retries other 4xx."""
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            resp = await client.get(url, params=params, timeout=timeout, follow_redirects=True)
        except httpx.TimeoutException as exc:
            last = ToolError("timeout", f"The data source timed out after {timeout:g}s.")
            log.info("timeout on %s (attempt %d): %s", url, attempt + 1, exc)
        except httpx.TransportError as exc:
            last = ToolError("upstream_error", "Couldn't reach the data source.")
            log.info("transport error on %s (attempt %d): %s", url, attempt + 1, exc)
        else:
            if resp.status_code < 400:
                return resp.text if as_text else resp.json()
            if resp.status_code not in RETRY_STATUS:
                raise UpstreamHTTPError(resp.status_code, _safe_json(resp))
            last = ToolError("upstream_error", f"The data source returned HTTP {resp.status_code}.")
        if attempt < retries:
            await asyncio.sleep(backoff * (2**attempt))
    assert last is not None
    raise last


class UpstreamHTTPError(Exception):
    """A non-retryable 4xx response, with the decoded body when there is one."""

    def __init__(self, status: int, body: Any) -> None:
        super().__init__(f"HTTP {status}: {body}")
        self.status = status
        self.body = body


def _safe_json(resp: httpx.Response) -> Any:
    try:
        return resp.json()
    except ValueError:
        return resp.text[:200]
