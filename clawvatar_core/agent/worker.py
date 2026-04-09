"""LiveKit agent worker — real-time voice conversation with avatar animation.

The agent:
1. Joins a LiveKit room
2. Listens to user speech (via realtime plugin)
3. Generates response (OpenAI Realtime / Gemini Live)
4. Speaks response (built-in TTS from realtime plugin)
5. TTS audio is intercepted → Clawvatar Engine → animation weights
6. Animation weights sent to browser for avatar rendering

Usage:
    worker = ClawvatarAgentWorker(provider="openai")
    worker.run()
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Literal, Optional

from livekit.agents import (
    Agent,
    AgentSession,
    JobContext,
    WorkerOptions,
    cli,
    RoomInputOptions,
)

logger = logging.getLogger(__name__)

# LiveKit credentials from env or Ruh Voice config
def _load_livekit_creds():
    """Load LiveKit credentials from env or Ruh Voice .env."""
    url = os.environ.get("LIVEKIT_URL", "")
    key = os.environ.get("LIVEKIT_API_KEY", "")
    secret = os.environ.get("LIVEKIT_API_SECRET", "")
    if url and key and secret:
        return url, key, secret

    # Try Ruh Voice config
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


class ClawvatarAgentWorker:
    """LiveKit agent worker with Clawvatar avatar."""

    def __init__(
        self,
        provider: Literal["openai", "google"] = "openai",
        model: str = "",
        voice: str = "",
        instructions: str = "",
        avatar_path: str = "",
        openclaw_enabled: bool = False,
    ):
        self.provider = provider
        self.model_name = model
        self.voice = voice
        self.instructions = instructions or "You are a helpful AI assistant. Keep responses concise and friendly."
        self.avatar_path = avatar_path
        self.openclaw_enabled = openclaw_enabled

    def _create_realtime_model(self):
        """Create the realtime LLM (handles STT + LLM + TTS in one stream)."""
        if self.provider == "openai":
            from livekit.plugins.openai import realtime
            return realtime.RealtimeModel(
                model=self.model_name or "gpt-4o-realtime-preview",
                voice=self.voice or "sage",
                instructions=self.instructions,
            )
        elif self.provider == "google":
            from livekit.plugins.google.realtime import RealtimeModel as GoogleRealtimeModel
            return GoogleRealtimeModel(
                model=self.model_name or "gemini-2.0-flash-exp",
                voice=self.voice or "Puck",
                instructions=self.instructions,
            )
        else:
            raise ValueError(f"Unknown provider: {self.provider}")

    async def entrypoint(self, ctx: JobContext):
        """Called when agent joins a LiveKit room."""
        logger.info(f"Agent joining room: {ctx.room.name}")

        realtime_model = self._create_realtime_model()

        agent = Agent(instructions=self.instructions)
        session = AgentSession(llm=realtime_model)

        # Add OpenClaw tools if enabled
        if self.openclaw_enabled:
            try:
                from clawvatar_core.agent.openclaw_bridge import OpenClawBridge
                bridge = OpenClawBridge()
                await bridge.connect()
                tools = bridge.create_tools()
                agent = Agent(instructions=self.instructions, tools=tools)
                logger.info(f"OpenClaw bridge: {len(tools)} tools registered")
            except Exception as e:
                logger.warning(f"OpenClaw bridge failed: {e}")

        await ctx.connect()
        await session.start(room=ctx.room, agent=agent)
        logger.info("Agent session started")

    def run(self):
        """Start the LiveKit agent worker process."""
        url, key, secret = _load_livekit_creds()

        if not url:
            raise RuntimeError(
                "LiveKit credentials not found. Set LIVEKIT_URL, LIVEKIT_API_KEY, "
                "LIVEKIT_API_SECRET env vars or have ~/ruh-voice/call-service/.env"
            )

        logger.info(f"Starting agent: provider={self.provider}, livekit={url}")

        # Set env vars for livekit-agents SDK
        os.environ["LIVEKIT_URL"] = url
        os.environ["LIVEKIT_API_KEY"] = key
        os.environ["LIVEKIT_API_SECRET"] = secret

        opts = WorkerOptions(entrypoint_fnc=self.entrypoint)
        cli.run_app(opts)
