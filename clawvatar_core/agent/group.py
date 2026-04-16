"""Group call — spawn multiple agents into a single LiveKit room.

Each agent runs as a SEPARATE subprocess (not in-process with the server)
to avoid crashing FastAPI. Each subprocess connects to the same LiveKit room
with its own identity and Gemini voice session.
"""

from __future__ import annotations

import asyncio
import datetime
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Voice assignments for different agents
VOICE_MAP = {
    "system-architect": "Kore",
    "vp-developer": "Puck",
    "vp-devops": "Charon",
    "vp-reviewer": "Fenrir",
    "vp-tester": "Aoede",
    "vp-manager": "Leda",
    "main": "Puck",
}


def _make_token(room_name: str, identity: str) -> tuple[str, str]:
    """Generate a LiveKit token for an agent participant."""
    from livekit.api import AccessToken, VideoGrants
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
    """A single agent participant in a group call — runs as subprocess."""

    def __init__(self, agent_id: str, room_name: str):
        self.agent_id = agent_id
        self.room_name = room_name
        self.identity = f"agent-{agent_id}"
        self._process: Optional[subprocess.Popen] = None

    async def join(self):
        """Spawn a subprocess that connects this agent to the room."""
        token, url = _make_token(self.room_name, self.identity)
        voice = VOICE_MAP.get(self.agent_id, "Puck")

        # Spawn the agent as a separate process
        script = Path(__file__).parent / "_group_worker.py"
        env = {**os.environ}
        env["CLAWVATAR_ROOM_TOKEN"] = token
        env["CLAWVATAR_ROOM_URL"] = url
        env["CLAWVATAR_AGENT_ID"] = self.agent_id
        env["CLAWVATAR_AGENT_VOICE"] = voice
        env["CLAWVATAR_ROOM_NAME"] = self.room_name

        self._process = subprocess.Popen(
            [sys.executable, str(script)],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        logger.info(
            f"Agent {self.agent_id} spawned (PID {self._process.pid}) "
            f"for room {self.room_name} as {self.identity}"
        )

    async def leave(self):
        """Kill the agent subprocess."""
        if self._process and self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
            logger.info(f"Agent {self.agent_id} left room {self.room_name}")
        self._process = None

    @property
    def is_connected(self) -> bool:
        return self._process is not None and self._process.poll() is None


class GroupCall:
    """Manages a group call with multiple agents in one room."""

    def __init__(self, room_name: str):
        self.room_name = room_name
        self.agents: dict[str, GroupCallAgent] = {}
        self._started = False

    async def add_agent(self, agent_id: str):
        if agent_id in self.agents:
            return
        gca = GroupCallAgent(agent_id, self.room_name)
        self.agents[agent_id] = gca
        if self._started:
            await gca.join()

    async def remove_agent(self, agent_id: str):
        gca = self.agents.pop(agent_id, None)
        if gca:
            await gca.leave()

    async def start(self):
        self._started = True
        for gca in self.agents.values():
            await gca.join()
            # Small delay between spawns to avoid resource contention
            await asyncio.sleep(0.5)

    async def stop(self):
        for gca in self.agents.values():
            await gca.leave()
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
