"""Room manager — creates LiveKit rooms and generates tokens for browser clients.

When a user wants to talk to the agent:
1. Create/get a LiveKit room
2. Generate a token for the browser to join
3. Agent worker auto-joins when room is created
4. Browser joins with WebRTC (voice + receives animation)
"""

from __future__ import annotations

import logging
import os
import time

from livekit.api import LiveKitAPI, AccessToken, VideoGrants

logger = logging.getLogger(__name__)


def _load_creds():
    url = os.environ.get("LIVEKIT_URL", "")
    key = os.environ.get("LIVEKIT_API_KEY", "")
    secret = os.environ.get("LIVEKIT_API_SECRET", "")
    if url and key and secret:
        return url, key, secret
    env_path = os.path.expanduser("~/ruh-voice/call-service/.env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line.startswith("LIVEKIT_URL="):
                    url = line.split("=", 1)[1]
                elif line.startswith("LIVEKIT_API_KEY="):
                    key = line.split("=", 1)[1]
                elif line.startswith("LIVEKIT_API_SECRET="):
                    secret = line.split("=", 1)[1]
    return url, key, secret


def generate_token(room_name: str, identity: str = "user", ttl: int = 3600) -> tuple[str, str]:
    """Generate a LiveKit access token for a participant.

    Returns:
        (token, livekit_url)
    """
    url, key, secret = _load_creds()
    if not key or not secret:
        raise RuntimeError("LiveKit credentials not found")

    token = AccessToken(api_key=key, api_secret=secret)
    token.identity = identity
    token.name = identity
    token.add_grant(VideoGrants(
        room_join=True,
        room=room_name,
        can_publish=True,
        can_subscribe=True,
    ))
    token.ttl = ttl

    jwt = token.to_jwt()
    logger.info(f"Token generated: room={room_name} identity={identity}")
    return jwt, url


def create_room_name(prefix: str = "clawvatar") -> str:
    """Generate a unique room name."""
    return f"{prefix}-{int(time.time())}"
