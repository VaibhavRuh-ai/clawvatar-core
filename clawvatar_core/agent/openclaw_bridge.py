"""OpenClaw task bridge — LiveKit agent delegates complex tasks to OpenClaw agents.

The LiveKit agent handles real-time conversation. When it needs to:
- Write code → sends to vp-developer
- Review code → sends to vp-reviewer
- Run tests → sends to vp-tester
- Deploy → sends to vp-devops
- Plan features → sends to vp-manager

The bridge sends the task, waits for result, and returns it to the conversation.
"""

from __future__ import annotations

import logging
from typing import Optional

from livekit.agents import llm

from clawvatar_core.adapters.openclaw import OpenClawAdapter

logger = logging.getLogger(__name__)


class OpenClawBridge:
    """Provides LiveKit agent with tools to delegate tasks to OpenClaw agents."""

    def __init__(self, adapter: Optional[OpenClawAdapter] = None):
        self._adapter = adapter

    async def connect(self) -> None:
        """Connect to OpenClaw gateway using settings from DB."""
        if self._adapter is None:
            try:
                from clawvatar_core import db
                url = db.get_setting("openclaw_url")
                token = db.get_setting("openclaw_token")
                if url and token:
                    self._adapter = OpenClawAdapter(gateway_url=url, token=token)
                else:
                    url, token = OpenClawAdapter.read_config()
                    self._adapter = OpenClawAdapter(gateway_url=url, token=token)
            except Exception:
                url, token = OpenClawAdapter.read_config()
                self._adapter = OpenClawAdapter(gateway_url=url, token=token)

        await self._adapter.connect()
        logger.info("OpenClaw bridge connected")

    async def disconnect(self) -> None:
        if self._adapter:
            await self._adapter.disconnect()

    async def send_task(self, agent_id: str, task: str, timeout: float = 60) -> str:
        """Send a task to an OpenClaw agent and wait for the result."""
        if not self._adapter or not self._adapter.is_connected:
            await self.connect()

        result = await self._adapter.send_to_agent(agent_id, task, timeout=timeout)
        text = result.get("text", "")
        error = result.get("error", "")

        if error:
            return f"Error from {agent_id}: {error}"
        return text or f"No response from {agent_id}"

    def create_tools(self) -> list:
        """Create LLM function tools for OpenClaw task delegation.

        These tools let the realtime LLM call OpenClaw agents when needed.
        Returns a list of FunctionTool objects created via @llm.function_tool.
        """
        bridge = self

        @llm.function_tool
        async def delegate_to_developer(task: str) -> str:
            """Send a coding task to the development agent. Use for writing code, fixing bugs, or implementing features."""
            return await bridge.send_task("vp-developer", task)

        @llm.function_tool
        async def delegate_to_reviewer(task: str) -> str:
            """Send code for review to the review agent. Use when code needs to be checked for quality."""
            return await bridge.send_task("vp-reviewer", task)

        @llm.function_tool
        async def delegate_to_tester(task: str) -> str:
            """Send a testing task to the QA agent. Use for writing or running tests."""
            return await bridge.send_task("vp-tester", task)

        @llm.function_tool
        async def delegate_to_devops(task: str) -> str:
            """Send a deployment task to the DevOps agent. Use for deploying, pushing, or infrastructure tasks."""
            return await bridge.send_task("vp-devops", task)

        @llm.function_tool
        async def delegate_to_manager(task: str) -> str:
            """Send a planning task to the project manager. Use for feature planning, task breakdown, or prioritization."""
            return await bridge.send_task("vp-manager", task)

        return [
            delegate_to_developer,
            delegate_to_reviewer,
            delegate_to_tester,
            delegate_to_devops,
            delegate_to_manager,
        ]
