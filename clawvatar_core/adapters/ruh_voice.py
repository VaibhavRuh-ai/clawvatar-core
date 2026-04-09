"""Ruh Voice adapter — integrates with LiveKit-based voice calls.

Provides avatar video track in LiveKit rooms alongside voice calls.

Example:
    adapter = RuhVoiceAdapter(livekit_url="wss://livekit.ruh.ai", ...)
    await adapter.on_call_start(room_name, agent_id)
    await adapter.on_tts_audio(room_name, agent_id, audio_chunk)
    await adapter.on_call_end(room_name, agent_id)
"""

from __future__ import annotations

import logging
from typing import Optional

from clawvatar_core.config import CoreConfig
from clawvatar_core.session import AvatarSession
from clawvatar_core.session_manager import SessionManager
from clawvatar_core.sinks.websocket_sink import WebSocketSink

logger = logging.getLogger(__name__)


class RuhVoiceAdapter:
    """Manages avatar sessions for Ruh Voice LiveKit calls."""

    def __init__(
        self,
        livekit_url: str = "",
        api_key: str = "",
        api_secret: str = "",
        config: CoreConfig | None = None,
    ):
        self.livekit_url = livekit_url
        self.api_key = api_key
        self.api_secret = api_secret
        self.config = config or CoreConfig()
        self._manager = SessionManager(self.config)
        # room_name -> agent_id mapping
        self._rooms: dict[str, str] = {}

    async def on_call_start(self, room_name: str, agent_id: str) -> AvatarSession:
        """Agent joins a call — create avatar session.

        In production, this would also create a LiveKit video track sink.
        For now, creates a WebSocket sink for browser viewing.
        """
        logger.info(f"Call started: room={room_name} agent={agent_id}")
        self._rooms[room_name] = agent_id

        # Create session with WebSocket sink (and LiveKit sink when available)
        sinks = [WebSocketSink(port=8766)]

        # TODO: Add LiveKitSink when implemented
        # from clawvatar_core.sinks.livekit_sink import LiveKitSink
        # sinks.append(LiveKitSink(room_name, agent_id, self.livekit_url, ...))

        session = await self._manager.create_session(
            agent_id=agent_id, sinks=sinks
        )
        return session

    async def on_tts_audio(
        self,
        room_name: str,
        agent_id: str,
        audio_chunk: bytes,
        format: str = "pcm16",
        sample_rate: int = 16000,
        text: str = "",
    ) -> None:
        """TTS audio chunk received — feed to avatar session."""
        session = self._manager.get_session(agent_id)
        if not session:
            session = await self.on_call_start(room_name, agent_id)
        await session.feed_audio(audio_chunk, format, sample_rate)

    async def on_tts_complete(
        self,
        room_name: str,
        agent_id: str,
        full_audio: bytes,
        sample_rate: int = 16000,
        text: str = "",
    ) -> None:
        """Complete TTS audio available — use batch mode for best quality."""
        session = self._manager.get_session(agent_id)
        if not session:
            session = await self.on_call_start(room_name, agent_id)
        await session.speak(audio=full_audio, sample_rate=sample_rate, text=text)

    async def on_call_end(self, room_name: str, agent_id: str = "") -> None:
        """Call ended — cleanup session."""
        aid = agent_id or self._rooms.pop(room_name, "")
        if aid:
            await self._manager.destroy_session(aid)
            logger.info(f"Call ended: room={room_name} agent={aid}")

    async def stop(self) -> None:
        await self._manager.destroy_all()
