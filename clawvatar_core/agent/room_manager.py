"""Room manager — creates LiveKit rooms and generates tokens for browser clients."""

from __future__ import annotations

import datetime
import logging
import os
import time

from livekit.api import AccessToken, VideoGrants

logger = logging.getLogger(__name__)


def _load_creds():
    """Load LiveKit credentials from environment variables."""
    url = os.environ.get("LIVEKIT_URL", "")
    key = os.environ.get("LIVEKIT_API_KEY", "")
    secret = os.environ.get("LIVEKIT_API_SECRET", "")
    return url, key, secret


def generate_token(room_name: str, identity: str = "user", ttl: int = 3600) -> tuple[str, str]:
    """Generate a LiveKit access token.

    Returns: (jwt_token, livekit_url)
    """
    url, key, secret = _load_creds()
    if not key or not secret:
        raise RuntimeError("LiveKit credentials not found")

    token = (
        AccessToken(api_key=key, api_secret=secret)
        .with_identity(identity)
        .with_name(identity)
        .with_grants(VideoGrants(
            room_join=True,
            room=room_name,
            can_publish=True,
            can_subscribe=True,
        ))
        .with_ttl(datetime.timedelta(seconds=ttl))
    )

    jwt = token.to_jwt()
    logger.info(f"Token generated: room={room_name} identity={identity}")
    return jwt, url


def create_room_name(prefix: str = "clawvatar") -> str:
    return f"{prefix}-{int(time.time())}"
