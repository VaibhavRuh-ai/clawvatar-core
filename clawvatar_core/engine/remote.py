"""Remote engine client — connects to clawvatar-engine WebSocket server."""

from __future__ import annotations

import base64
import json
import logging

import numpy as np
import websockets

from clawvatar_core.config import EngineConfig
from clawvatar_core.engine.client import EngineClient

logger = logging.getLogger(__name__)


class RemoteEngineClient(EngineClient):
    """Connects to a running clawvatar-engine server via WebSocket."""

    def __init__(self, config: EngineConfig):
        self._config = config
        self._ws = None
        self._connected = False

    def _url(self) -> str:
        proto = "wss" if self._config.ssl else "ws"
        return f"{proto}://{self._config.host}:{self._config.port}/ws"

    async def connect(self) -> None:
        import ssl as ssl_mod

        kwargs = {}
        if self._config.ssl:
            ctx = ssl_mod.SSLContext(ssl_mod.PROTOCOL_TLS_CLIENT)
            ctx.check_hostname = False
            ctx.verify_mode = ssl_mod.CERT_NONE
            kwargs["ssl"] = ctx

        self._ws = await websockets.connect(self._url(), **kwargs)
        self._connected = True
        logger.info(f"Remote engine connected: {self._url()}")

    async def disconnect(self) -> None:
        if self._ws:
            await self._ws.close()
            self._ws = None
        self._connected = False

    async def _send_recv(self, msg: dict) -> dict:
        if not self._ws:
            await self.connect()
        await self._ws.send(json.dumps(msg))
        resp = json.loads(await self._ws.recv())
        if resp.get("type") == "error":
            raise RuntimeError(resp.get("message", "Unknown engine error"))
        return resp

    async def load_avatar(self, path: str) -> dict:
        resp = await self._send_recv({"type": "avatar.load", "model_path": path})
        return resp.get("info", {})

    async def process_audio(self, chunk: np.ndarray, sample_rate: int = 16000) -> dict:
        pcm16 = (chunk * 32767).astype(np.int16)
        b64 = base64.b64encode(pcm16.tobytes()).decode()
        return await self._send_recv({"type": "audio", "data": b64, "sample_rate": sample_rate})

    async def process_batch(
        self, audio: np.ndarray, sample_rate: int = 16000, chunk_size: int = 1024
    ) -> dict:
        pcm16 = (audio * 32767).astype(np.int16)
        b64 = base64.b64encode(pcm16.tobytes()).decode()
        return await self._send_recv({
            "type": "audio.batch",
            "data": b64,
            "sample_rate": sample_rate,
            "chunk_size": chunk_size,
        })

    async def get_idle(self) -> dict:
        return await self._send_recv({"type": "idle"})

    @property
    def is_connected(self) -> bool:
        return self._connected and self._ws is not None
