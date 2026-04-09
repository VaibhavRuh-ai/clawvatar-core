"""Animation sink base — abstract interface for output delivery targets."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class AnimationFrame:
    """One frame of avatar animation data."""
    weights: dict[str, float] = field(default_factory=dict)
    head_yaw: float = 0.0
    head_pitch: float = 0.0
    head_roll: float = 0.0
    viseme: str = "REST"
    is_speaking: bool = False
    emotion: str = "neutral"
    emotion_intensity: float = 0.0
    timestamp: float = 0.0

    @classmethod
    def from_engine_response(cls, data: dict) -> AnimationFrame:
        """Create from clawvatar-engine weight response."""
        head = data.get("head", {})
        return cls(
            weights=data.get("weights", data.get("w", {})),
            head_yaw=head.get("yaw", head.get("y", 0)),
            head_pitch=head.get("pitch", head.get("p", 0)),
            head_roll=head.get("roll", head.get("r", 0)),
            viseme=data.get("viseme", data.get("v", "REST")),
            is_speaking=data.get("is_speaking", data.get("s", False)),
            emotion=data.get("emotion", "neutral"),
            emotion_intensity=data.get("emotion_intensity", 0),
        )

    def to_ws_message(self) -> dict:
        """Convert to WebSocket message format (compatible with engine's test UI)."""
        return {
            "type": "weights",
            "weights": self.weights,
            "head": {"yaw": self.head_yaw, "pitch": self.head_pitch, "roll": self.head_roll},
            "viseme": self.viseme,
            "is_speaking": self.is_speaking,
            "emotion": self.emotion,
            "emotion_intensity": self.emotion_intensity,
        }


class AnimationSink(ABC):
    """Abstract output target for avatar animation frames."""

    @abstractmethod
    async def start(self) -> None:
        """Initialize the sink."""

    @abstractmethod
    async def stop(self) -> None:
        """Cleanup the sink."""

    @abstractmethod
    async def send_frame(self, frame: AnimationFrame) -> None:
        """Send a single animation frame."""

    async def send_batch(self, frames: list[AnimationFrame], audio_b64: str = "",
                         sample_rate: int = 16000) -> None:
        """Send a batch of frames with optional audio. Default: send one by one."""
        for frame in frames:
            await self.send_frame(frame)
