"""Engine client interface — abstract base for embedded and remote modes."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

import numpy as np


class EngineClient(ABC):
    """Abstract interface for communicating with clawvatar-engine."""

    @abstractmethod
    async def connect(self) -> None:
        """Initialize connection to the engine."""

    @abstractmethod
    async def disconnect(self) -> None:
        """Close connection."""

    @abstractmethod
    async def load_avatar(self, path: str) -> dict:
        """Load a VRM/GLB avatar model. Returns avatar info."""

    @abstractmethod
    async def process_audio(self, chunk: np.ndarray, sample_rate: int = 16000) -> dict:
        """Process a single audio chunk. Returns weights dict."""

    @abstractmethod
    async def process_batch(
        self, audio: np.ndarray, sample_rate: int = 16000, chunk_size: int = 1024
    ) -> dict:
        """Process entire audio at once. Returns dict with 'frames' list."""

    @abstractmethod
    async def get_idle(self) -> dict:
        """Get idle animation weights."""

    @property
    @abstractmethod
    def is_connected(self) -> bool:
        """Whether the engine is ready."""
