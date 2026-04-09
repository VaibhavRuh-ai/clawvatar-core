"""LiveKit agent worker — real-time voice conversation with avatar animation.

Uses LiveKit Agents SDK with OpenAI Realtime or Gemini Live plugins.
TTS audio is intercepted and sent to Clawvatar Engine for lip-sync.

Usage:
    from clawvatar_core.agent.worker import ClawvatarAgent
    agent = ClawvatarAgent(provider="openai")
    agent.run()  # starts LiveKit agent worker
"""

from __future__ import annotations

import asyncio
import base64
import logging
from typing import Literal, Optional

from livekit import rtc
from livekit.agents import (
    Agent,
    AgentSession,
    JobContext,
    WorkerOptions,
    cli,
    llm,
)

logger = logging.getLogger(__name__)


def create_realtime_model(
    provider: Literal["openai", "google"] = "openai",
    model: str = "",
    voice: str = "",
    instructions: str = "",
):
    """Create a realtime LLM model (OpenAI Realtime or Gemini Live)."""
    if provider == "openai":
        from livekit.plugins.openai import realtime

        return realtime.RealtimeModel(
            model=model or "gpt-4o-realtime-preview",
            voice=voice or "sage",
            instructions=instructions or "You are a helpful AI assistant. Be concise and friendly.",
        )
    elif provider == "google":
        from livekit.plugins.google import beta as google_beta

        return google_beta.RealtimeModel(
            model=model or "gemini-2.0-flash-exp",
            voice=voice or "Puck",
            instructions=instructions or "You are a helpful AI assistant. Be concise and friendly.",
        )
    else:
        raise ValueError(f"Unknown provider: {provider}. Use 'openai' or 'google'.")


class ClawvatarAgentWorker:
    """LiveKit agent worker with Clawvatar avatar integration.

    Runs as a LiveKit agent that:
    1. Listens to user speech (via realtime plugin STT)
    2. Generates response (via realtime plugin LLM)
    3. Speaks response (via realtime plugin TTS)
    4. Animates avatar from TTS audio output
    """

    def __init__(
        self,
        provider: Literal["openai", "google"] = "openai",
        model: str = "",
        voice: str = "",
        instructions: str = "",
        avatar_path: str = "",
        openclaw_enabled: bool = False,
        livekit_url: str = "",
        livekit_api_key: str = "",
        livekit_api_secret: str = "",
    ):
        self.provider = provider
        self.model_name = model
        self.voice = voice
        self.instructions = instructions
        self.avatar_path = avatar_path
        self.openclaw_enabled = openclaw_enabled
        self.livekit_url = livekit_url
        self.livekit_api_key = livekit_api_key
        self.livekit_api_secret = livekit_api_secret

        # Engine for animation
        self._engine = None

    async def _setup_engine(self):
        """Initialize Clawvatar engine."""
        from clawvatar_core.engine.embedded import EmbeddedEngineClient
        from clawvatar_core.config import EngineConfig

        self._engine = EmbeddedEngineClient(EngineConfig())
        await self._engine.connect()

        if self.avatar_path:
            await self._engine.load_avatar(self.avatar_path)
            logger.info(f"Avatar loaded: {self.avatar_path}")

    async def entrypoint(self, ctx: JobContext):
        """LiveKit agent entrypoint — called when agent joins a room."""
        logger.info(f"Agent joining room: {ctx.room.name}")

        # Setup engine
        await self._setup_engine()

        # Create realtime model
        realtime_model = create_realtime_model(
            provider=self.provider,
            model=self.model_name,
            voice=self.voice,
            instructions=self.instructions,
        )

        # Create agent
        agent = Agent(instructions=self.instructions)
        session = AgentSession(
            llm=realtime_model,
        )

        # Connect to room
        await ctx.connect()

        # Start the agent session
        await session.start(
            room=ctx.room,
            agent=agent,
        )

        logger.info("Agent session started, listening for speech...")

    def run(self):
        """Start the LiveKit agent worker."""
        opts = WorkerOptions(
            entrypoint_fnc=self.entrypoint,
        )
        if self.livekit_url:
            opts.ws_url = self.livekit_url
        if self.livekit_api_key:
            opts.api_key = self.livekit_api_key
        if self.livekit_api_secret:
            opts.api_secret = self.livekit_api_secret

        cli.run_app(opts)
