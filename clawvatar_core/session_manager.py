"""Session manager — multi-agent session registry."""

from __future__ import annotations

import logging
from typing import Optional

from clawvatar_core.avatar.store import AvatarStore
from clawvatar_core.config import CoreConfig
from clawvatar_core.engine.client import EngineClient
from clawvatar_core.engine.embedded import EmbeddedEngineClient
from clawvatar_core.engine.remote import RemoteEngineClient
from clawvatar_core.session import AvatarSession
from clawvatar_core.sinks.base import AnimationSink

logger = logging.getLogger(__name__)


class SessionManager:
    """Manages multiple avatar sessions across agents."""

    def __init__(self, config: CoreConfig | None = None):
        self.config = config or CoreConfig()
        self.avatar_store = AvatarStore(self.config.avatar_store.base_dir)
        self._sessions: dict[str, AvatarSession] = {}
        self._engine: Optional[EngineClient] = None

    def _get_engine(self) -> EngineClient:
        """Get or create the shared engine client."""
        if self._engine is None:
            if self.config.engine.mode == "embedded":
                self._engine = EmbeddedEngineClient(self.config.engine)
            else:
                self._engine = RemoteEngineClient(self.config.engine)
        return self._engine

    async def create_session(
        self,
        agent_id: str,
        sinks: list[AnimationSink] | None = None,
        avatar_path: str | None = None,
    ) -> AvatarSession:
        """Create a new avatar session for an agent.

        Looks up avatar assignment if no avatar_path provided.
        """
        if agent_id in self._sessions:
            logger.warning(f"Session already exists for {agent_id}, returning existing")
            return self._sessions[agent_id]

        engine = self._get_engine()
        session = AvatarSession(
            agent_id=agent_id,
            engine=engine,
            idle_fps=self.config.idle_fps,
        )

        if sinks:
            for sink in sinks:
                session.add_sink(sink)

        # Load avatar
        path = avatar_path
        if not path:
            path = self.avatar_store.get_avatar_path_for_agent(agent_id)
        if not path and self.config.avatar_store.default_avatar:
            path = self.config.avatar_store.default_avatar

        await session.start()

        if path:
            await session.load_avatar(path)

        self._sessions[agent_id] = session
        logger.info(f"Session created: {agent_id} (total: {len(self._sessions)})")
        return session

    def get_session(self, agent_id: str) -> Optional[AvatarSession]:
        """Get an active session."""
        return self._sessions.get(agent_id)

    async def destroy_session(self, agent_id: str) -> None:
        """Stop and remove a session."""
        session = self._sessions.pop(agent_id, None)
        if session:
            await session.stop()
            logger.info(f"Session destroyed: {agent_id}")

    async def destroy_all(self) -> None:
        """Stop all sessions."""
        for agent_id in list(self._sessions.keys()):
            await self.destroy_session(agent_id)
        if self._engine:
            await self._engine.disconnect()
            self._engine = None

    def list_sessions(self) -> list[dict]:
        """List all active sessions."""
        return [s.get_state() for s in self._sessions.values()]
