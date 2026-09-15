from __future__ import annotations

import json
import os
import sys

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

import httpx
import pytest
import respx
from httpx import Response

from infra.observability.analytics import PostHogClient


@pytest.mark.asyncio
@respx.mock
async def test_track_event_posts_to_posthog_capture_api(monkeypatch):
    monkeypatch.setenv("ANALYTICS_ENABLED", "true")
    monkeypatch.setenv("POSTHOG_API_KEY", "phc_test123")
    from infra.config import settings as sm
    sm.get_settings.cache_clear()

    route = respx.post("https://us.i.posthog.com/capture/").mock(return_value=Response(200, json={"status": 1}))

    client = PostHogClient()
    await client.track_event("user-1", "trial_activated", {"plan_name": "Trial"})

    assert route.called
    body = json.loads(route.calls[0].request.content)
    assert body["event"] == "trial_activated"
    assert body["distinct_id"] == "user-1"
    assert body["properties"] == {"plan_name": "Trial"}
    assert body["api_key"] == "phc_test123"


@pytest.mark.asyncio
@respx.mock
async def test_track_event_is_a_noop_when_analytics_disabled(monkeypatch):
    monkeypatch.setenv("ANALYTICS_ENABLED", "false")
    from infra.config import settings as sm
    sm.get_settings.cache_clear()

    route = respx.post("https://us.i.posthog.com/capture/").mock(return_value=Response(200))

    client = PostHogClient()
    await client.track_event("user-1", "trial_activated")

    assert not route.called


@pytest.mark.asyncio
@respx.mock
async def test_track_event_never_raises_when_posthog_is_down(monkeypatch):
    monkeypatch.setenv("ANALYTICS_ENABLED", "true")
    monkeypatch.setenv("POSTHOG_API_KEY", "phc_test123")
    from infra.config import settings as sm
    sm.get_settings.cache_clear()

    respx.post("https://us.i.posthog.com/capture/").mock(side_effect=httpx.ConnectTimeout("timeout"))

    client = PostHogClient()
    await client.track_event("user-1", "trial_activated")  # não deve levantar
