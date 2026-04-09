"""Composite sink — fan-out to multiple sinks simultaneously."""

from __future__ import annotations

import asyncio

from clawvatar_core.sinks.base import AnimationFrame, AnimationSink


class CompositeSink(AnimationSink):
    """Sends frames to all child sinks concurrently."""

    def __init__(self, sinks: list[AnimationSink] | None = None):
        self._sinks = list(sinks or [])

    def add(self, sink: AnimationSink) -> None:
        self._sinks.append(sink)

    def remove(self, sink: AnimationSink) -> None:
        self._sinks.remove(sink)

    async def start(self) -> None:
        await asyncio.gather(*(s.start() for s in self._sinks))

    async def stop(self) -> None:
        await asyncio.gather(*(s.stop() for s in self._sinks))

    async def send_frame(self, frame: AnimationFrame) -> None:
        await asyncio.gather(*(s.send_frame(frame) for s in self._sinks))

    async def send_batch(self, frames: list[AnimationFrame], audio_b64: str = "",
                         sample_rate: int = 16000) -> None:
        await asyncio.gather(*(
            s.send_batch(frames, audio_b64, sample_rate) for s in self._sinks
        ))
