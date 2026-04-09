"""OpenClaw adapter — connects to OpenClaw gateway for multi-agent avatar support.

Listens for agent speech events and creates avatar sessions automatically.

Example:
    adapter = OpenClawAdapter(
        gateway_url="ws://localhost:18789",
        token="openclaw-auth-token",
    )
    await adapter.start()
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from clawvatar_core.config import CoreConfig
from clawvatar_core.session import AvatarSession
from clawvatar_core.session_manager import SessionManager
from clawvatar_core.sinks.websocket_sink import WebSocketSink

logger = logging.getLogger(__name__)


class OpenClawAdapter:
    """Connects to OpenClaw gateway and provides avatar sessions for agents."""

    def __init__(
        self,
        gateway_url: str = "ws://localhost:18789",
        token: str = "",
        config: CoreConfig | None = None,
        ws_base_port: int = 8766,
    ):
        self.gateway_url = gateway_url
        self.token = token
        self.config = config or CoreConfig()
        self.ws_base_port = ws_base_port
        self._manager = SessionManager(self.config)
        self._ws = None

    async def start(self) -> None:
        """Connect to OpenClaw gateway and start listening."""
        logger.info(f"OpenClaw adapter connecting to {self.gateway_url}")
        # TODO: Implement OpenClaw gateway WebSocket connection
        # This would:
        # 1. Connect to gateway with token auth
        # 2. Listen for agent speech events
        # 3. Auto-create sessions for speaking agents
        # 4. Route TTS audio to sessions
        logger.info("OpenClaw adapter started (integration pending full gateway protocol)")

    async def on_agent_speak(
        self, agent_id: str, audio: bytes, sample_rate: int = 16000, text: str = ""
    ) -> None:
        """Called when an OpenClaw agent speaks. Feed audio to its avatar session."""
        session = self._manager.get_session(agent_id)
        if not session:
            # Auto-create session with WebSocket sink
            sink = WebSocketSink(port=self.ws_base_port)
            session = await self._manager.create_session(
                agent_id=agent_id, sinks=[sink]
            )
        await session.speak(audio=audio, sample_rate=sample_rate, text=text)

    async def on_agent_stream(
        self, agent_id: str, chunk: bytes, format: str = "pcm16", sample_rate: int = 16000
    ) -> None:
        """Called when TTS audio streams for an agent."""
        session = self._manager.get_session(agent_id)
        if not session:
            sink = WebSocketSink(port=self.ws_base_port)
            session = await self._manager.create_session(
                agent_id=agent_id, sinks=[sink]
            )
        await session.feed_audio(chunk, format, sample_rate)

    async def stop(self) -> None:
        await self._manager.destroy_all()
        if self._ws:
            await self._ws.close()
