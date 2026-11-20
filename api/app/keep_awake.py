"""Keep a free Render instance from going to sleep.

Render stops a free web service after 15 minutes without an inbound request, and
the next visitor then waits up to a minute while it starts again. A request to the
service's own public address counts as inbound, so the service makes one itself
every few minutes. Nothing outside has to remember to: a scheduled CI job ran
hours late and failed whenever Render was still waking up.

On only where Render has given the service a public address
(``RENDER_EXTERNAL_URL``, which Render sets itself) and ``KEEP_AWAKE_MINUTES`` is
not 0. Every worker runs it; two small requests every ten minutes cost nothing.
"""

from __future__ import annotations

import asyncio
import logging

import httpx

from app.config import Settings

logger = logging.getLogger("rasta.api.keep_awake")

#: A wake-up request that has not answered in this long is abandoned.
REQUEST_TIMEOUT_SECONDS = 30.0


class KeepAwake:
    """Requests ``url`` every ``interval`` seconds until stopped."""

    def __init__(
        self,
        url: str,
        *,
        interval: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._url = url
        self._interval = interval
        self._transport = transport
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="keep-awake")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            await self._task

    async def _loop(self) -> None:
        async with httpx.AsyncClient(
            timeout=REQUEST_TIMEOUT_SECONDS, transport=self._transport
        ) as client:
            while not await self._stopped_within(self._interval):
                try:
                    response = await client.get(self._url)
                except httpx.HTTPError as error:
                    logger.warning(
                        "keep-awake request failed: %s", type(error).__name__
                    )
                    continue
                if response.status_code >= 400:
                    logger.warning(
                        "keep-awake request answered %d", response.status_code
                    )

    async def _stopped_within(self, seconds: float) -> bool:
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)
        except TimeoutError:
            return False
        return True


def keep_awake_for(settings: Settings) -> KeepAwake | None:
    """The wake-up timer for this deployment, or None where there is nothing to wake."""

    base = settings.render_external_url.strip().rstrip("/")
    if settings.keep_awake_minutes <= 0 or not base:
        return None
    return KeepAwake(f"{base}/health", interval=settings.keep_awake_minutes * 60.0)


__all__ = ["KeepAwake", "keep_awake_for"]
