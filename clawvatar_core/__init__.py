"""Clawvatar Core — AI agent avatar integration with LiveKit, OpenClaw, and real-time voice."""

__version__ = "0.1.0"

from clawvatar_core.config import CoreConfig
from clawvatar_core.session import AvatarSession
from clawvatar_core.session_manager import SessionManager

__all__ = ["CoreConfig", "AvatarSession", "SessionManager"]
