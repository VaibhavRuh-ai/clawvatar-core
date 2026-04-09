"""WebSocket sink — pushes animation frames to browser clients via WebSocket.

Compatible with clawvatar-engine's test UI (Three.js + three-vrm).
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional

from clawvatar_core.sinks.base import AnimationFrame, AnimationSink

logger = logging.getLogger(__name__)


class WebSocketSink(AnimationSink):
    """Sends animation frames to connected WebSocket clients.

    Runs a WebSocket server that browsers connect to.
    Uses the same protocol as clawvatar-engine's test UI.
    """

    def __init__(self, host: str = "0.0.0.0", port: int = 8766):
        self.host = host
        self.port = port
        self._server = None
        self._clients: set = set()

    async def start(self) -> None:
        import websockets.server

        self._server = await websockets.server.serve(
            self._handler, self.host, self.port
        )
        logger.info(f"WebSocket sink listening on ws://{self.host}:{self.port}")

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()
        self._clients.clear()

    async def send_frame(self, frame: AnimationFrame) -> None:
        """Send frame to all connected clients."""
        if not self._clients:
            return
        msg = json.dumps(frame.to_ws_message())
        dead = set()
        for ws in self._clients:
            try:
                await ws.send(msg)
            except Exception:
                dead.add(ws)
        self._clients -= dead

    async def send_batch(self, frames: list[AnimationFrame], audio_b64: str = "",
                         sample_rate: int = 16000) -> None:
        """Send batch to all clients (for sync playback in browser)."""
        if not self._clients:
            return
        batch_frames = []
        for f in frames:
            batch_frames.append({
                "w": f.weights,
                "h": {"yaw": f.head_yaw, "pitch": f.head_pitch, "roll": f.head_roll},
                "v": f.viseme,
                "s": f.is_speaking,
            })
        msg = json.dumps({
            "type": "batch_weights",
            "frames": batch_frames,
            "audio_b64": audio_b64,
            "sample_rate": sample_rate,
            "duration": frames[-1].timestamp if frames else 0,
        })
        dead = set()
        for ws in self._clients:
            try:
                await ws.send(msg)
            except Exception:
                dead.add(ws)
        self._clients -= dead

    async def _handler(self, ws, path=None):
        self._clients.add(ws)
        logger.info(f"WebSocket client connected ({len(self._clients)} total)")
        try:
            async for msg in ws:
                # Handle client messages (ping, avatar.load, etc.)
                try:
                    data = json.loads(msg)
                    if data.get("type") == "ping":
                        await ws.send(json.dumps({"type": "pong"}))
                except Exception:
                    pass
        except Exception:
            pass
        finally:
            self._clients.discard(ws)
            logger.info(f"WebSocket client disconnected ({len(self._clients)} total)")

    @property
    def client_count(self) -> int:
        return len(self._clients)
