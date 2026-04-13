"""Idle Director — uses Gemini Flash Lite to give the avatar life during pauses.

Architecture:
- During active speech: NOT called (voice latency untouched)
- During idle: called every 5-15s to pick contextual actions
- Returns structured action dict for the avatar to perform

The director never runs in the voice path, so it cannot affect latency.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

# Action library — what the avatar can do
LOOK_TARGETS = ["user", "window", "art", "plant", "bookshelf", "floor", "ceiling", "side_left", "side_right"]
GESTURES = ["none", "scratch_chin", "cross_arms", "lean_back", "lean_forward",
            "tilt_head_left", "tilt_head_right", "stretch", "fold_hands", "look_at_hands"]
EXPRESSIONS = ["neutral", "thoughtful", "curious", "amused", "focused", "relaxed", "surprised"]


SYSTEM_PROMPT = """You are a stage director for an AI avatar in a video call. Pick natural, subtle body language for the idle moments between conversation.

Available looks: user, window, art, plant, bookshelf, floor, ceiling, side_left, side_right
Available gestures: none, scratch_chin, cross_arms, lean_back, lean_forward, tilt_head_left, tilt_head_right, stretch, fold_hands, look_at_hands
Available expressions: neutral, thoughtful, curious, amused, focused, relaxed, surprised

Respond ONLY with a single JSON object. No prose. No markdown. No backticks.

Example:
{"look": "window", "gesture": "scratch_chin", "expression": "thoughtful", "duration": 4}"""


class IdleDirector:
    """Calls Gemini Flash Lite to pick avatar actions during idle moments."""

    def __init__(self, api_key: str = "", model: str = "gemini-2.0-flash-lite"):
        self.api_key = api_key or os.environ.get("GOOGLE_API_KEY", "")
        self.model = model
        self._client = None

    def _get_client(self):
        if self._client is None and self.api_key:
            from google import genai
            self._client = genai.Client(api_key=self.api_key)
        return self._client

    async def pick_action(
        self,
        agent_id: str = "",
        last_user_message: str = "",
        last_agent_message: str = "",
        idle_seconds: float = 5.0,
        last_action: str = "",
    ) -> dict:
        """Get a director action from the LLM. Returns dict with look/gesture/expression/duration.

        On error, returns a safe fallback action.
        """
        client = self._get_client()
        if client is None:
            return self._fallback(idle_seconds)

        # Build context — keep it tight for speed
        context = f"Agent: {agent_id or 'assistant'}\n"
        if last_user_message:
            context += f"Last user: {last_user_message[:200]}\n"
        if last_agent_message:
            context += f"Last agent reply: {last_agent_message[:200]}\n"
        context += f"Idle: {int(idle_seconds)}s\n"
        if last_action:
            context += f"Last action: {last_action} (avoid repeating)\n"
        context += "Pick one natural idle action."

        try:
            from google.genai import types

            response = await asyncio.to_thread(
                client.models.generate_content,
                model=self.model,
                contents=context,
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT,
                    response_mime_type="application/json",
                    temperature=0.9,
                    max_output_tokens=100,
                ),
            )
            text = response.text.strip()
            data = json.loads(text)
            return self._validate(data)
        except Exception as e:
            logger.warning(f"Director error: {e}")
            return self._fallback(idle_seconds)

    def _validate(self, data: dict) -> dict:
        """Sanitize director output to safe values."""
        look = data.get("look", "user")
        if look not in LOOK_TARGETS:
            look = "user"
        gesture = data.get("gesture", "none")
        if gesture not in GESTURES:
            gesture = "none"
        expression = data.get("expression", "neutral")
        if expression not in EXPRESSIONS:
            expression = "neutral"
        duration = float(data.get("duration", 4))
        duration = max(2.0, min(8.0, duration))
        return {"look": look, "gesture": gesture, "expression": expression, "duration": duration}

    def _fallback(self, idle_seconds: float) -> dict:
        """Safe rule-based fallback if LLM fails."""
        import random
        looks = ["user", "window", "art", "side_left", "side_right"]
        gestures = ["none", "tilt_head_left", "tilt_head_right", "fold_hands"]
        return {
            "look": random.choice(looks),
            "gesture": random.choice(gestures),
            "expression": "neutral",
            "duration": 4,
        }
