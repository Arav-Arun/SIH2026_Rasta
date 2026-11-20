from __future__ import annotations

import asyncio

import httpx
from app.config import Settings
from app.keep_awake import KeepAwake, keep_awake_for


def test_it_is_off_where_render_has_not_given_an_address() -> None:
    assert keep_awake_for(Settings(render_external_url="")) is None


def test_it_can_be_turned_off_on_render() -> None:
    settings = Settings(
        render_external_url="https://rasta-api.onrender.com", keep_awake_minutes=0
    )
    assert keep_awake_for(settings) is None


def test_it_requests_the_services_own_health_check_until_stopped() -> None:
    seen: list[str] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200 if len(seen) == 1 else 503)

    async def scenario() -> None:
        timer = KeepAwake(
            "https://rasta-api.onrender.com/health",
            interval=0.01,
            transport=httpx.MockTransport(answer),
        )
        timer.start()
        await asyncio.sleep(0.1)
        await timer.stop()

    asyncio.run(scenario())
    # A failed answer is logged, never raised, and the timer keeps going.
    assert len(seen) >= 2
    assert set(seen) == {"https://rasta-api.onrender.com/health"}


def test_the_address_comes_from_render() -> None:
    timer = keep_awake_for(
        Settings(render_external_url="https://rasta-api.onrender.com/")
    )
    assert timer is not None
    assert timer._url == "https://rasta-api.onrender.com/health"
