"""Agent configuration."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class AgentConfig(BaseModel):
    """Configuration for the Clawvatar LiveKit agent."""

    # LiveKit
    livekit_url: str = ""
    livekit_api_key: str = ""
    livekit_api_secret: str = ""

    # LLM Provider
    provider: Literal["openai", "google"] = "openai"
    model: str = ""  # auto-selects default per provider
    voice: str = ""  # auto-selects default per provider
    instructions: str = "You are a helpful AI assistant. Be concise and friendly."

    # Avatar
    avatar_path: str = ""

    # OpenClaw integration
    openclaw_enabled: bool = False
    openclaw_gateway_url: str = ""
    openclaw_token: str = ""

    # API Keys (can also be set via env vars)
    openai_api_key: str = ""
    google_api_key: str = ""
