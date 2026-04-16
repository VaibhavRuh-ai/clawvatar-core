"""Clawvatar agent runner — lightweight LiveKit agent that uses SOUL.md per OpenClaw agent.

Reads credentials from the SQLite DB (~/.clawvatar/clawvatar.db) instead of hardcoding them.
Room name format: agentid-timestamp → extracts agent_id → reads SOUL.md.

Usage:
    python -m clawvatar_core.agent.run
    # or from the CLI:
    clawvatar-core agent
"""

import logging
import os
import sys
from pathlib import Path

# Load credentials from DB before importing livekit (which reads env vars)
def _load_credentials():
    try:
        from clawvatar_core import db
        settings = db.get_all_settings()
        env_map = {
            "google_api_key": "GOOGLE_API_KEY",
            "livekit_url": "LIVEKIT_URL",
            "livekit_api_key": "LIVEKIT_API_KEY",
            "livekit_api_secret": "LIVEKIT_API_SECRET",
            "openai_api_key": "OPENAI_API_KEY",
        }
        for db_key, env_key in env_map.items():
            val = settings.get(db_key, "")
            if val and not os.environ.get(env_key):
                os.environ[env_key] = val
    except Exception as e:
        logging.warning(f"Could not load credentials from DB: {e}")


_load_credentials()

sys.argv = [sys.argv[0], "start"]
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("clawvatar-agent")

from livekit.agents import Agent, AgentSession, JobContext, WorkerOptions, cli
from livekit.plugins.google.realtime import RealtimeModel


def read_soul(agent_id: str) -> str:
    """Read SOUL.md from OpenClaw workspace."""
    p = Path(os.path.expanduser("~/.openclaw")) / f"workspace-{agent_id}" / "SOUL.md"
    return p.read_text() if p.exists() else ""


async def entrypoint(ctx: JobContext):
    room_name = ctx.room.name
    agent_id = room_name.rsplit("-", 1)[0] if "-" in room_name else ""
    soul = read_soul(agent_id) if agent_id else ""
    logger.info(f"Room: {room_name}, Agent: {agent_id}, SOUL: {len(soul)} chars")

    if soul:
        instructions = (
            f"You are the {agent_id} agent. "
            f"Speak and behave according to your role. "
            f"Keep voice responses concise (1-3 sentences). "
            f"When asked complex tasks (write code, review, test, deploy, plan), "
            f"tell the user you'll delegate it.\n\nYOUR ROLE:\n{soul[:3000]}"
        )
    else:
        instructions = f"You are the {agent_id or 'assistant'}. Be helpful and concise."

    model = RealtimeModel(
        model="gemini-2.5-flash-native-audio-latest",
        voice="Puck",
        instructions=instructions,
    )
    agent = Agent(instructions=instructions)
    session = AgentSession(llm=model)
    await ctx.connect()
    await session.start(room=ctx.room, agent=agent)


def main():
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))


if __name__ == "__main__":
    main()
