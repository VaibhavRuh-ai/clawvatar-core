"""LiveKit agent worker — uses OpenClaw agent's SOUL.md and delegates tasks.

The worker:
1. Registers with LiveKit Cloud
2. When a room is created, reads the agent config from DB
3. Uses the OpenClaw agent's SOUL.md as its personality
4. Has function tools to delegate tasks to OpenClaw agents
5. Responds with voice (Gemini Live / OpenAI Realtime)
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
)

logger = logging.getLogger(__name__)


def _load_livekit_creds():
    """Load LiveKit credentials from environment variables."""
    url = os.environ.get("LIVEKIT_URL", "")
    key = os.environ.get("LIVEKIT_API_KEY", "")
    secret = os.environ.get("LIVEKIT_API_SECRET", "")
    return url, key, secret


def _load_soul_md(agent_id: str) -> str:
    """Load SOUL.md for an agent — from DB settings or filesystem."""
    try:
        from clawvatar_core import db
        return db.read_soul_md(agent_id)
    except Exception:
        return ""


def _build_instructions(agent_id: str, soul_md: str) -> str:
    """Build agent instructions from SOUL.md."""
    if not soul_md:
        return f"You are the {agent_id} agent. Be helpful and concise."

    # Take key parts of SOUL.md — first 3000 chars
    truncated = soul_md[:3000]
    return (
        f"You are the {agent_id} agent in the OpenClaw system. "
        f"You speak and behave according to your role defined below. "
        f"Keep voice responses concise (1-3 sentences). "
        f"When the user asks you to do complex tasks like writing code, "
        f"reviewing code, running tests, or deploying — use your delegation tools "
        f"to send those tasks to the appropriate OpenClaw agent.\n\n"
        f"YOUR ROLE (from SOUL.md):\n{truncated}"
    )


async def _create_openclaw_tools():
    """Create function tools for delegating to OpenClaw agents."""
    try:
        from clawvatar_core.agent.openclaw_bridge import OpenClawBridge
        bridge = OpenClawBridge()
        await bridge.connect()
        tools = bridge.create_tools()
        logger.info(f"OpenClaw bridge connected: {len(tools)} delegation tools")
        return tools
    except Exception as e:
        logger.warning(f"OpenClaw bridge not available: {e}")
        return []


class ClawvatarAgentWorker:
    """LiveKit agent worker with SOUL.md personality and OpenClaw delegation."""

    def __init__(
        self,
        provider: Literal["openai", "google"] = "google",
        model: str = "",
        voice: str = "",
        instructions: str = "",
        default_agent_id: str = "",
        openclaw_enabled: bool = True,
    ):
        self.provider = provider
        self.model_name = model
        self.voice = voice
        self.instructions = instructions
        self.default_agent_id = default_agent_id
        self.openclaw_enabled = openclaw_enabled

    def _create_model(self, instructions: str):
        """Create the realtime LLM model."""
        if self.provider == "openai":
            from livekit.plugins.openai import realtime
            return realtime.RealtimeModel(
                model=self.model_name or "gpt-4o-realtime-preview",
                voice=self.voice or "sage",
                instructions=instructions,
            )
        else:
            from livekit.plugins.google.realtime import RealtimeModel
            return RealtimeModel(
                model=self.model_name or "gemini-2.5-flash-native-audio-latest",
                voice=self.voice or "Puck",
                instructions=instructions,
            )

    async def entrypoint(self, ctx: JobContext):
        """Called when agent joins a LiveKit room."""
        room_name = ctx.room.name
        logger.info(f"Agent joining room: {room_name}")

        # Extract agent_id from room name (format: "agentid-timestamp")
        agent_id = self.default_agent_id
        if not agent_id and "-" in room_name:
            agent_id = room_name.rsplit("-", 1)[0]

        # Load SOUL.md
        soul_md = _load_soul_md(agent_id) if agent_id else ""
        if soul_md:
            logger.info(f"SOUL.md loaded for {agent_id}: {len(soul_md)} chars")
        else:
            logger.info(f"No SOUL.md for {agent_id}")

        # Build instructions
        if self.instructions:
            instructions = self.instructions
        else:
            instructions = _build_instructions(agent_id, soul_md)

        # Create model
        model = self._create_model(instructions)

        # Create OpenClaw delegation tools
        tools = []
        if self.openclaw_enabled:
            tools = await _create_openclaw_tools()

        # Create agent with tools
        agent = Agent(instructions=instructions, tools=tools if tools else None)
        session = AgentSession(llm=model)

        await ctx.connect()
        await session.start(room=ctx.room, agent=agent)
        logger.info(f"Agent started: {agent_id}, provider={self.provider}, tools={len(tools)}")

    def run(self):
        """Start the LiveKit agent worker process."""
        url, key, secret = _load_livekit_creds()
        if not url:
            raise RuntimeError("Set LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET")

        logger.info(f"Starting worker: provider={self.provider}, openclaw={self.openclaw_enabled}")
        opts = WorkerOptions(entrypoint_fnc=self.entrypoint)
        cli.run_app(opts)
