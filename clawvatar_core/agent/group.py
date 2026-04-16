"""Group call — spawn multiple agents into a single LiveKit room.

Each agent joins as a separate participant with its own SOUL.md personality
and Gemini voice session. Users can talk and all agents hear them.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Optional

from livekit import rtc
from livekit.agents import Agent, AgentSession
from livekit.api import AccessToken, VideoGrants
from livekit.plugins.google.realtime import RealtimeModel

logger = logging.getLogger(__name__)

# Voice assignments for different agents (so they sound distinct)
VOICE_MAP = {
    "system-architect": "Kore",
    "vp-developer": "Puck",
    "vp-devops": "Charon",
    "vp-reviewer": "Fenrir",
    "vp-tester": "Aoede",
    "vp-manager": "Leda",
    "main": "Puck",
}


def _read_soul(agent_id: str) -> str:
    """Read SOUL.md from OpenClaw workspace."""
    p = Path(os.path.expanduser("~/.openclaw")) / f"workspace-{agent_id}" / "SOUL.md"
    return p.read_text() if p.exists() else ""


def _make_token(room_name: str, identity: str) -> tuple[str, str]:
    """Generate a LiveKit token for an agent participant."""
    import datetime
    url = os.environ.get("LIVEKIT_URL", "")
    key = os.environ.get("LIVEKIT_API_KEY", "")
    secret = os.environ.get("LIVEKIT_API_SECRET", "")
    if not key or not secret:
        raise RuntimeError("LiveKit credentials not configured")

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
        .with_ttl(datetime.timedelta(seconds=3600))
    )
    return token.to_jwt(), url


class GroupCallAgent:
    """A single agent participant in a group call."""

    def __init__(self, agent_id: str, room_name: str):
        self.agent_id = agent_id
        self.room_name = room_name
        self.identity = f"agent-{agent_id}"
        self._room: Optional[rtc.Room] = None
        self._session: Optional[AgentSession] = None
        self._task: Optional[asyncio.Task] = None

    async def join(self):
        """Connect this agent to the room and start voice session."""
        soul = _read_soul(self.agent_id)
        voice = VOICE_MAP.get(self.agent_id, "Puck")

        # Build instructions
        instructions = (
            f"You are {self.agent_id} in a group call with other agents and a user. "
            f"Keep responses concise (1-2 sentences). "
            f"Only respond when the user addresses you by name or your expertise area. "
            f"If the question is for another agent, stay silent. "
        )
        if soul:
            instructions += f"\n\nYOUR ROLE:\n{soul[:2500]}"
        else:
            instructions += f"\nYou are a helpful {self.agent_id} assistant."

        # Connect to room
        token, url = _make_token(self.room_name, self.identity)
        self._room = rtc.Room()
        await self._room.connect(url, token)
        logger.info(f"Agent {self.agent_id} joined room {self.room_name} as {self.identity}")

        # Start voice session
        model = RealtimeModel(
            model="gemini-2.5-flash-native-audio-latest",
            voice=voice,
            instructions=instructions,
        )
        agent = Agent(instructions=instructions)
        self._session = AgentSession(llm=model)
        await self._session.start(room=self._room, agent=agent)

    async def leave(self):
        """Disconnect this agent from the room."""
        if self._room:
            await self._room.disconnect()
            self._room = None
        self._session = None
        logger.info(f"Agent {self.agent_id} left room {self.room_name}")

    @property
    def is_connected(self) -> bool:
        return self._room is not None and self._room.connection_state == rtc.ConnectionState.CONN_CONNECTED


class GroupCall:
    """Manages a group call with multiple agents in one room."""

    def __init__(self, room_name: str):
        self.room_name = room_name
        self.agents: dict[str, GroupCallAgent] = {}
        self._started = False

    async def add_agent(self, agent_id: str):
        """Add an agent to the group call."""
        if agent_id in self.agents:
            logger.warning(f"Agent {agent_id} already in call")
            return

        gca = GroupCallAgent(agent_id, self.room_name)
        self.agents[agent_id] = gca

        if self._started:
            # Call already active — join immediately
            await gca.join()

    async def remove_agent(self, agent_id: str):
        """Remove an agent from the group call."""
        gca = self.agents.pop(agent_id, None)
        if gca:
            await gca.leave()

    async def start(self):
        """Start the group call — all agents join the room."""
        self._started = True
        # Join all agents concurrently
        tasks = [gca.join() for gca in self.agents.values()]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for agent_id, result in zip(self.agents.keys(), results):
            if isinstance(result, Exception):
                logger.error(f"Agent {agent_id} failed to join: {result}")

    async def stop(self):
        """Stop the group call — all agents leave."""
        tasks = [gca.leave() for gca in self.agents.values()]
        await asyncio.gather(*tasks, return_exceptions=True)
        self.agents.clear()
        self._started = False

    def get_status(self) -> dict:
        return {
            "room": self.room_name,
            "agents": [
                {"id": aid, "connected": gca.is_connected, "identity": gca.identity}
                for aid, gca in self.agents.items()
            ],
            "active": self._started,
        }
