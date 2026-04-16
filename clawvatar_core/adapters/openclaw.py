"""OpenClaw adapter — connects to OpenClaw gateway, bridges agents to avatar video calls.

Full flow:
  User speaks → STT → text → OpenClaw Agent → response text → TTS → Engine → Avatar animation

Usage:
    adapter = OpenClawAdapter(gateway_url="ws://localhost:18789/ws", token="...")
    await adapter.connect()
    agents = await adapter.list_agents()
    response = await adapter.send_to_agent("vp-manager", "Hello!")
    # response contains agent's text reply
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Optional

import websockets

logger = logging.getLogger(__name__)

# Reconnection config
MAX_RECONNECT_DELAY = 30  # seconds
INITIAL_RECONNECT_DELAY = 1  # seconds


class OpenClawAdapter:
    """Connects to OpenClaw gateway and communicates with agents.

    Features auto-reconnect with exponential backoff on connection loss.
    """

    def __init__(
        self,
        gateway_url: str = "ws://localhost:18789/ws",
        token: str = "",
        origin: str = "http://localhost:18789",
        auto_reconnect: bool = True,
    ):
        self.gateway_url = gateway_url
        self.token = token
        self.origin = origin
        self.auto_reconnect = auto_reconnect
        self._ws = None
        self._connected = False
        self._intentional_close = False
        self._pending: dict[str, asyncio.Future] = {}
        self._event_handlers: list = []
        self._response_collectors: dict[str, list] = {}
        self._recv_task: Optional[asyncio.Task] = None
        self._reconnect_task: Optional[asyncio.Task] = None
        self._reconnect_delay = INITIAL_RECONNECT_DELAY

    async def connect(self) -> dict:
        """Connect to OpenClaw gateway and authenticate.

        Returns:
            Hello payload from gateway.
        """
        self._intentional_close = False

        try:
            self._ws = await asyncio.wait_for(
                websockets.connect(
                    self.gateway_url,
                    origin=self.origin,
                    ping_interval=20,
                    ping_timeout=10,
                ),
                timeout=10,
            )
        except Exception as e:
            logger.error(f"WebSocket connect failed: {e}")
            self._schedule_reconnect()
            raise

        try:
            # Wait for challenge
            r = json.loads(await asyncio.wait_for(self._ws.recv(), timeout=10))
            if r.get("event") != "connect.challenge":
                raise RuntimeError(f"Expected challenge, got: {r}")

            # Send connect
            rid = self._make_id()
            await self._ws.send(json.dumps({
                "type": "req",
                "id": rid,
                "method": "connect",
                "params": {
                    "minProtocol": 3,
                    "maxProtocol": 3,
                    "client": {
                        "id": "openclaw-control-ui",
                        "version": "clawvatar-core-0.1",
                        "platform": "linux",
                        "mode": "webchat",
                    },
                    "role": "operator",
                    "scopes": ["operator.read", "operator.write"],
                    "auth": {"token": self.token},
                    "caps": ["tool-events"],
                },
            }))

            r = json.loads(await asyncio.wait_for(self._ws.recv(), timeout=10))
            if not r.get("ok"):
                err = r.get("error", {}).get("message", "Connect failed")
                raise RuntimeError(f"OpenClaw connect failed: {err}")

            hello = r.get("payload", {})
            self._connected = True
            self._reconnect_delay = INITIAL_RECONNECT_DELAY  # reset backoff on success

            # Start background receiver
            self._recv_task = asyncio.create_task(self._recv_loop())

            logger.info(f"Connected to OpenClaw gateway: {self.gateway_url}")
            return hello

        except Exception as e:
            if self._ws:
                await self._ws.close()
                self._ws = None
            self._schedule_reconnect()
            raise

    async def disconnect(self) -> None:
        """Intentionally disconnect — does NOT auto-reconnect."""
        self._intentional_close = True
        self._connected = False
        if self._reconnect_task:
            self._reconnect_task.cancel()
            self._reconnect_task = None
        if self._recv_task:
            self._recv_task.cancel()
        if self._ws:
            await self._ws.close()

    async def list_agents(self) -> list[dict]:
        """Get all agents from gateway."""
        await self._ensure_connected()
        result = await self._request("agents.list", {})
        return result.get("agents", [])

    async def send_to_agent(
        self,
        agent_id: str,
        message: str,
        scope: str = "main",
        timeout: float = 30.0,
    ) -> dict:
        """Send a message to an OpenClaw agent and collect the response.

        Returns:
            Dict with "text" (agent's reply), "events" (raw events), "agent_id".
        """
        await self._ensure_connected()

        req_id = self._make_id()
        self._response_collectors[req_id] = []

        key = f"agent:{agent_id}:{scope}"
        await self._request("sessions.send", {
            "key": key,
            "message": message,
        }, req_id=req_id)

        # Collect streaming response events
        collected_text = []
        seen_count = 0
        error_msg = ""
        end_time = asyncio.get_event_loop().time() + timeout

        while asyncio.get_event_loop().time() < end_time:
            events = self._response_collectors.get(req_id, [])
            new_events = events[seen_count:]
            seen_count = len(events)

            for evt in new_events:
                evt_type = evt.get("event", "")
                payload = evt.get("payload", {})
                data = payload.get("data", {})
                stream = payload.get("stream", "")

                if evt_type == "agent" and stream == "text":
                    delta = data.get("delta", "") if isinstance(data, dict) else ""
                    if delta:
                        collected_text.append(delta)

                if evt_type == "chat" and payload.get("state") == "complete":
                    text = "".join(collected_text).strip()
                    del self._response_collectors[req_id]
                    return {"text": text, "agent_id": agent_id, "events": events}

                if evt_type == "agent" and stream == "lifecycle":
                    phase = data.get("phase", "") if isinstance(data, dict) else ""
                    if phase == "end":
                        text = "".join(collected_text).strip()
                        del self._response_collectors[req_id]
                        return {"text": text, "agent_id": agent_id, "events": events}
                    elif phase == "error":
                        error_msg = data.get("error", "Agent error") if isinstance(data, dict) else "Agent error"

                if "text" in payload:
                    collected_text.append(payload["text"])

            await asyncio.sleep(0.1)

        # Timeout
        text = "".join(collected_text).strip()
        self._response_collectors.pop(req_id, None)
        if not text and error_msg:
            return {"text": "", "error": error_msg, "agent_id": agent_id, "events": []}
        return {"text": text or "(no response)", "agent_id": agent_id, "events": []}

    # ---- Connection management ----

    async def _ensure_connected(self):
        """Reconnect if disconnected. Raises if unable."""
        if self._connected and self._ws:
            return
        logger.info("Reconnecting to OpenClaw...")
        await self.connect()

    def _schedule_reconnect(self):
        """Schedule a reconnect attempt with exponential backoff."""
        if not self.auto_reconnect or self._intentional_close:
            return
        if self._reconnect_task and not self._reconnect_task.done():
            return  # already scheduled

        async def _reconnect():
            while not self._connected and not self._intentional_close:
                delay = self._reconnect_delay
                logger.info(f"Reconnecting to OpenClaw in {delay}s...")
                await asyncio.sleep(delay)
                self._reconnect_delay = min(delay * 2, MAX_RECONNECT_DELAY)
                try:
                    await self.connect()
                    logger.info("Reconnected to OpenClaw")
                    return
                except Exception as e:
                    logger.warning(f"Reconnect failed: {e}")

        try:
            self._reconnect_task = asyncio.create_task(_reconnect())
        except RuntimeError:
            pass  # no event loop running

    # ---- Internal ----

    async def _request(self, method: str, params: dict, req_id: str = None) -> dict:
        """Send a request to gateway and wait for response."""
        if not self._ws:
            raise RuntimeError("Not connected")

        rid = req_id or self._make_id()
        fut = asyncio.get_event_loop().create_future()
        self._pending[rid] = fut

        await self._ws.send(json.dumps({
            "type": "req",
            "id": rid,
            "method": method,
            "params": params,
        }))

        try:
            result = await asyncio.wait_for(fut, timeout=10)
        except asyncio.TimeoutError:
            self._pending.pop(rid, None)
            raise RuntimeError(f"Request timeout: {method}")

        return result

    async def _recv_loop(self) -> None:
        """Background task: receive and route gateway messages."""
        try:
            async for raw in self._ws:
                try:
                    msg = json.loads(raw)
                    msg_type = msg.get("type", "")

                    if msg_type == "res":
                        rid = msg.get("id", "")
                        fut = self._pending.pop(rid, None)
                        if fut and not fut.done():
                            if msg.get("ok"):
                                fut.set_result(msg.get("payload", {}))
                            else:
                                fut.set_exception(RuntimeError(
                                    msg.get("error", {}).get("message", "Request failed")
                                ))

                    elif msg_type == "event":
                        for rid, collector in self._response_collectors.items():
                            collector.append(msg)
                        for handler in self._event_handlers:
                            try:
                                handler(msg)
                            except Exception:
                                pass

                except json.JSONDecodeError:
                    pass
        except websockets.exceptions.ConnectionClosed:
            logger.warning("Gateway connection closed")
        except asyncio.CancelledError:
            return
        except Exception as e:
            logger.error(f"Recv loop error: {e}")
        finally:
            self._connected = False
            # Fail all pending requests
            for rid, fut in self._pending.items():
                if not fut.done():
                    fut.set_exception(RuntimeError("Connection lost"))
            self._pending.clear()
            # Auto-reconnect
            self._schedule_reconnect()

    def on_event(self, handler) -> None:
        """Register an event handler."""
        self._event_handlers.append(handler)

    @property
    def is_connected(self) -> bool:
        return self._connected

    @staticmethod
    def _make_id() -> str:
        return uuid.uuid4().hex[:8]

    @staticmethod
    def read_config() -> tuple[str, str]:
        """Read gateway URL and token from ~/.openclaw/openclaw.json."""
        import os
        config_path = os.path.expanduser("~/.openclaw/openclaw.json")
        with open(config_path) as f:
            config = json.load(f)
        port = config.get("gateway", {}).get("port", 18789)
        token = config.get("gateway", {}).get("auth", {}).get("token", "")
        return f"ws://localhost:{port}/ws", token
