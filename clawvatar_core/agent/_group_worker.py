"""Group call worker — spawned as a subprocess per agent.

Connects to a LiveKit room with a specific identity and starts
a Gemini voice session with the agent's SOUL.md personality.

Environment variables (set by group.py):
  CLAWVATAR_ROOM_TOKEN  — LiveKit JWT token
  CLAWVATAR_ROOM_URL    — LiveKit server URL
  CLAWVATAR_AGENT_ID    — Agent ID (for SOUL.md lookup)
  CLAWVATAR_AGENT_VOICE — Gemini voice name
  CLAWVATAR_ROOM_NAME   — Room name (for logging)
"""

import asyncio
import logging
import os
import signal
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("group-worker")


def read_soul(agent_id: str) -> str:
    p = Path(os.path.expanduser("~/.openclaw")) / f"workspace-{agent_id}" / "SOUL.md"
    return p.read_text() if p.exists() else ""


async def run_agent():
    token = os.environ.get("CLAWVATAR_ROOM_TOKEN", "")
    url = os.environ.get("CLAWVATAR_ROOM_URL", "")
    agent_id = os.environ.get("CLAWVATAR_AGENT_ID", "assistant")
    voice = os.environ.get("CLAWVATAR_AGENT_VOICE", "Puck")
    room_name = os.environ.get("CLAWVATAR_ROOM_NAME", "")

    if not token or not url:
        logger.error("Missing CLAWVATAR_ROOM_TOKEN or CLAWVATAR_ROOM_URL")
        sys.exit(1)

    soul = read_soul(agent_id)
    logger.info(f"Agent {agent_id} starting, voice={voice}, soul={len(soul)} chars, room={room_name}")

    instructions = (
        f"You are {agent_id} in a group call with other agents and a user. "
        f"Keep responses concise (1-2 sentences). "
        f"Only respond when the user addresses you by name or your expertise area. "
        f"If the question is for another agent, stay silent. "
    )
    if soul:
        instructions += f"\n\nYOUR ROLE:\n{soul[:2500]}"
    else:
        instructions += f"\nYou are a helpful {agent_id} assistant."

    from livekit import rtc
    from livekit.agents import Agent, AgentSession
    from livekit.plugins.google.realtime import RealtimeModel

    model = RealtimeModel(
        model="gemini-2.5-flash-native-audio-latest",
        voice=voice,
        instructions=instructions,
    )

    room = rtc.Room()
    await room.connect(url, token)
    logger.info(f"Agent {agent_id} connected to room {room_name}")

    agent = Agent(instructions=instructions)
    session = AgentSession(llm=model)
    await session.start(room=room, agent=agent)
    logger.info(f"Agent {agent_id} voice session started")

    # Keep running until killed
    stop = asyncio.Event()

    def on_signal(*_):
        stop.set()

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)

    try:
        await stop.wait()
    except asyncio.CancelledError:
        pass
    finally:
        await room.disconnect()
        logger.info(f"Agent {agent_id} disconnected")


if __name__ == "__main__":
    asyncio.run(run_agent())
