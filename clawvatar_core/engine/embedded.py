"""Embedded engine client — runs clawvatar-engine in-process. Lowest latency."""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np

from clawvatar_core.config import EngineConfig
from clawvatar_core.engine.client import EngineClient

logger = logging.getLogger(__name__)


class EmbeddedEngineClient(EngineClient):
    """Wraps ClawvatarEngine in the same process. ~7ms per chunk."""

    def __init__(self, config: EngineConfig):
        self._config = config
        self._engine = None
        self._connected = False

    async def connect(self) -> None:
        from clawvatar import ClawvatarEngine
        from clawvatar.config import ClawvatarConfig

        # Build engine config from our core config
        engine_config = ClawvatarConfig()
        engine_config.lipsync.provider = self._config.lipsync_provider
        engine_config.render.width = self._config.render_width
        engine_config.render.height = self._config.render_height
        engine_config.render.fps = self._config.render_fps

        self._engine = ClawvatarEngine(config=engine_config)
        self._engine.setup()
        self._connected = True
        logger.info("Embedded engine connected")

    async def disconnect(self) -> None:
        if self._engine:
            self._engine.cleanup()
            self._engine = None
        self._connected = False

    async def load_avatar(self, path: str) -> dict:
        if not self._engine:
            await self.connect()
        return self._engine.load_avatar(path)

    async def process_audio(self, chunk: np.ndarray, sample_rate: int = 16000) -> dict:
        if not self._engine:
            await self.connect()
        return self._engine.process_audio(chunk, sample_rate)

    async def process_batch(
        self, audio: np.ndarray, sample_rate: int = 16000, chunk_size: int = 1024
    ) -> dict:
        if not self._engine:
            await self.connect()
        return self._engine.process_batch(audio, sample_rate, chunk_size)

    async def get_idle(self) -> dict:
        if not self._engine:
            await self.connect()
        return self._engine.get_idle()

    @property
    def is_connected(self) -> bool:
        return self._connected
