"""Avatar session — central orchestrator for one agent's avatar animation."""

from __future__ import annotations

import asyncio
import base64
import logging
import time
from enum import Enum
from typing import AsyncIterator, Optional

import numpy as np

from clawvatar_core.audio.collector import AudioCollector
from clawvatar_core.engine.client import EngineClient
from clawvatar_core.sinks.base import AnimationFrame, AnimationSink
from clawvatar_core.sinks.composite import CompositeSink

logger = logging.getLogger(__name__)


class SessionState(str, Enum):
    IDLE = "idle"
    SPEAKING = "speaking"
    STOPPED = "stopped"


class AvatarSession:
    """Manages one agent's avatar — idle animation, speaking, output delivery.

    Usage:
        session = AvatarSession(agent_id="my-agent", engine=engine_client)
        session.add_sink(websocket_sink)
        await session.start()
        await session.speak(text="Hello!", audio=pcm_bytes, sample_rate=16000)
        await session.stop()
    """

    def __init__(
        self,
        agent_id: str,
        engine: EngineClient,
        idle_fps: int = 10,
    ):
        self.agent_id = agent_id
        self.engine = engine
        self.idle_fps = idle_fps
        self.state = SessionState.STOPPED
        self._sinks = CompositeSink()
        self._collector = AudioCollector()
        self._idle_task: Optional[asyncio.Task] = None
        self._avatar_loaded = False

    def add_sink(self, sink: AnimationSink) -> None:
        self._sinks.add(sink)

    async def start(self) -> None:
        """Initialize engine, start sinks, begin idle loop."""
        if not self.engine.is_connected:
            await self.engine.connect()
        await self._sinks.start()
        self.state = SessionState.IDLE
        self._idle_task = asyncio.create_task(self._idle_loop())
        logger.info(f"Session started: {self.agent_id}")

    async def stop(self) -> None:
        """Stop everything, cleanup."""
        self.state = SessionState.STOPPED
        if self._idle_task:
            self._idle_task.cancel()
            try:
                await self._idle_task
            except asyncio.CancelledError:
                pass
        await self._sinks.stop()
        logger.info(f"Session stopped: {self.agent_id}")

    async def load_avatar(self, path: str) -> dict:
        """Load a VRM/GLB avatar into the engine."""
        info = await self.engine.load_avatar(path)
        self._avatar_loaded = True
        logger.info(f"Avatar loaded for {self.agent_id}: {info.get('name', path)}")
        return info

    async def speak(
        self,
        audio: bytes | np.ndarray,
        sample_rate: int = 16000,
        text: str = "",
    ) -> None:
        """Batch mode: send complete utterance, animate in sync.

        Audio plays back through sinks with pre-computed animation.
        """
        self.state = SessionState.SPEAKING

        # Normalize audio
        if isinstance(audio, bytes):
            audio_f32 = self._collector.feed_pcm16(audio, sample_rate)
        else:
            audio_f32 = self._collector.feed_float32(audio, sample_rate)

        # Process batch through engine
        result = await self.engine.process_batch(audio_f32, sample_rate=16000)
        frames_data = result.get("frames", [])

        # Convert to AnimationFrames
        frames = []
        chunk_dur = 1024 / 16000  # engine chunk size
        for i, fd in enumerate(frames_data):
            f = AnimationFrame.from_engine_response(fd)
            f.timestamp = i * chunk_dur
            frames.append(f)

        # Send batch with audio to sinks
        pcm16 = (audio_f32 * 32767).astype(np.int16)
        audio_b64 = base64.b64encode(pcm16.tobytes()).decode()
        await self._sinks.send_batch(frames, audio_b64, 16000)

        self._collector.clear()
        self.state = SessionState.IDLE
        logger.info(f"Speak done: {self.agent_id}, {len(frames)} frames")

    async def feed_audio(
        self,
        chunk: bytes | np.ndarray,
        format: str = "pcm16",
        sample_rate: int = 16000,
    ) -> None:
        """Streaming mode: feed audio chunk by chunk, animate in real-time."""
        self.state = SessionState.SPEAKING

        if isinstance(chunk, bytes):
            audio_f32 = self._collector.feed_bytes(chunk, format, sample_rate)
        else:
            audio_f32 = self._collector.feed_float32(chunk, sample_rate)

        # Process through engine
        result = await self.engine.process_audio(audio_f32, sample_rate=16000)
        frame = AnimationFrame.from_engine_response(result)
        frame.timestamp = time.time()

        await self._sinks.send_frame(frame)

    async def speak_stream(
        self,
        audio_stream: AsyncIterator[bytes],
        format: str = "pcm16",
        sample_rate: int = 16000,
    ) -> None:
        """Hybrid: stream TTS audio chunks, animate as they arrive."""
        self.state = SessionState.SPEAKING
        async for chunk in audio_stream:
            await self.feed_audio(chunk, format, sample_rate)
        self.state = SessionState.IDLE

    async def _idle_loop(self) -> None:
        """Background: send idle weights (blink, breathe, sway) when not speaking."""
        while self.state != SessionState.STOPPED:
            if self.state == SessionState.IDLE:
                try:
                    idle = await self.engine.get_idle()
                    if idle:
                        frame = AnimationFrame.from_engine_response(idle)
                        await self._sinks.send_frame(frame)
                except Exception:
                    pass
            await asyncio.sleep(1.0 / self.idle_fps)

    def get_state(self) -> dict:
        """Current session state for monitoring."""
        return {
            "agent_id": self.agent_id,
            "state": self.state.value,
            "avatar_loaded": self._avatar_loaded,
            "sink_count": len(self._sinks._sinks),
        }
