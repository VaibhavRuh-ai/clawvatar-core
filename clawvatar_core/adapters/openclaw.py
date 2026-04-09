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
from typing import AsyncIterator, Optional

import websockets

logger = logging.getLogger(__name__)


class OpenClawAdapter:
    """Connects to OpenClaw gateway and communicates with agents."""

    def __init__(
        self,
        gateway_url: str = "ws://localhost:18789/ws",
        token: str = "",
        origin: str = "http://localhost:18789",
    ):
        self.gateway_url = gateway_url
        self.token = token
        self.origin = origin
        self._ws = None
        self._connected = False
        self._pending: dict[str, asyncio.Future] = {}
        self._event_handlers: list = []
        self._response_collectors: dict[str, list] = {}  # req_id → collected events
        self._recv_task: Optional[asyncio.Task] = None

    async def connect(self) -> dict:
        """Connect to OpenClaw gateway and authenticate.

        Returns:
            Hello payload from gateway.
        """
        self._ws = await websockets.connect(
            self.gateway_url,
            origin=self.origin,
            ping_interval=20,
            ping_timeout=10,
        )

        # Wait for challenge
        r = json.loads(await self._ws.recv())
        if r.get("event") != "connect.challenge":
            raise RuntimeError(f"Expected challenge, got: {r}")

        # Send connect (handle response directly since recv loop not yet running)
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

        # Start background receiver
        self._recv_task = asyncio.create_task(self._recv_loop())

        logger.info(f"Connected to OpenClaw gateway: {self.gateway_url}")
        return hello

    async def disconnect(self) -> None:
        self._connected = False
        if self._recv_task:
            self._recv_task.cancel()
        if self._ws:
            await self._ws.close()

    async def list_agents(self) -> list[dict]:
        """Get all agents from gateway."""
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
        req_id = self._make_id()
        self._response_collectors[req_id] = []

        # Send message
        await self._request("sessions.send", {
            "agent": agent_id,
            "message": message,
            "scope": scope,
        }, req_id=req_id)

        # Collect streaming response events
        collected_text = []
        end_time = asyncio.get_event_loop().time() + timeout

        while asyncio.get_event_loop().time() < end_time:
            events = self._response_collectors.get(req_id, [])
            new_events = events[len(collected_text):]

            for evt in new_events:
                evt_type = evt.get("event", "")
                payload = evt.get("payload", {})

                # Collect text from various event types
                if "text" in payload:
                    collected_text.append(payload["text"])
                elif "content" in payload:
                    collected_text.append(payload["content"])
                elif "delta" in payload:
                    collected_text.append(payload["delta"])

                # Check for completion events
                if evt_type in ("session.complete", "turn.complete", "response.done"):
                    text = "".join(collected_text).strip()
                    del self._response_collectors[req_id]
                    return {"text": text, "agent_id": agent_id, "events": events}

            await asyncio.sleep(0.1)

        # Timeout — return what we have
        text = "".join(collected_text).strip()
        self._response_collectors.pop(req_id, None)
        return {"text": text or "(no response)", "agent_id": agent_id, "events": []}

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
                        # Response to a request
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
                        # Broadcast event — route to collectors
                        for rid, collector in self._response_collectors.items():
                            collector.append(msg)

                        # Call registered handlers
                        for handler in self._event_handlers:
                            try:
                                handler(msg)
                            except Exception:
                                pass

                except json.JSONDecodeError:
                    pass
        except websockets.exceptions.ConnectionClosed:
            logger.info("Gateway connection closed")
        except asyncio.CancelledError:
            pass
        finally:
            self._connected = False

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
