"""Standalone adapter — direct Python API usage without any platform.

Example:
    from clawvatar_core.adapters.standalone import StandaloneAdapter

    adapter = StandaloneAdapter(avatar_path="avatar.vrm")
    await adapter.start()
    await adapter.speak(audio_bytes, sample_rate=16000)
    await adapter.stop()
"""

from __future__ import annotations

import logging
from typing import Optional

from clawvatar_core.config import CoreConfig
from clawvatar_core.session import AvatarSession
from clawvatar_core.session_manager import SessionManager
from clawvatar_core.sinks.base import AnimationSink
from clawvatar_core.sinks.websocket_sink import WebSocketSink

logger = logging.getLogger(__name__)


class StandaloneAdapter:
    """Simplest way to use clawvatar-core — no platform, just Python."""

    def __init__(
        self,
        avatar_path: str = "",
        agent_id: str = "default",
        config: CoreConfig | None = None,
        ws_port: int = 8766,
    ):
        self.config = config or CoreConfig()
        self.agent_id = agent_id
        self.avatar_path = avatar_path
        self._manager = SessionManager(self.config)
        self._ws_sink = WebSocketSink(port=ws_port)
        self._session: Optional[AvatarSession] = None

    async def start(self, sinks: list[AnimationSink] | None = None) -> None:
        """Start the adapter with optional extra sinks."""
        all_sinks = [self._ws_sink]
        if sinks:
            all_sinks.extend(sinks)

        self._session = await self._manager.create_session(
            agent_id=self.agent_id,
            sinks=all_sinks,
            avatar_path=self.avatar_path,
        )
        logger.info(f"Standalone adapter started. WebSocket: ws://0.0.0.0:{self._ws_sink.port}")

    async def speak(self, audio: bytes, sample_rate: int = 16000, text: str = "") -> None:
        """Send complete audio — avatar animates in sync."""
        if not self._session:
            raise RuntimeError("Call start() first")
        await self._session.speak(audio=audio, sample_rate=sample_rate, text=text)

    async def feed_audio(self, chunk: bytes, format: str = "pcm16", sample_rate: int = 16000) -> None:
        """Stream audio chunk by chunk."""
        if not self._session:
            raise RuntimeError("Call start() first")
        await self._session.feed_audio(chunk, format, sample_rate)

    async def stop(self) -> None:
        await self._manager.destroy_all()
