"""Core configuration for clawvatar-core."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field


class EngineConfig(BaseModel):
    """How to connect to clawvatar-engine."""
    mode: Literal["embedded", "remote"] = "embedded"
    # Remote mode
    host: str = "localhost"
    port: int = 8765
    ssl: bool = False
    # Embedded mode (passed through to engine)
    lipsync_provider: str = "energy"
    render_width: int = 512
    render_height: int = 512
    render_fps: int = 30


class AvatarStoreConfig(BaseModel):
    base_dir: str = str(Path.home() / ".clawvatar" / "avatars")
    default_avatar: str = ""


class ServerConfig(BaseModel):
    enabled: bool = True
    host: str = "0.0.0.0"
    port: int = 8766
    ssl_cert: str = ""
    ssl_key: str = ""


class CoreConfig(BaseModel):
    engine: EngineConfig = Field(default_factory=EngineConfig)
    avatar_store: AvatarStoreConfig = Field(default_factory=AvatarStoreConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)
    idle_fps: int = 10
    audio_buffer_ms: int = 200

    @classmethod
    def from_yaml(cls, path: str | Path) -> CoreConfig:
        path = Path(path)
        if not path.exists():
            return cls()
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        return cls(**data)

    def to_yaml(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            yaml.dump(self.model_dump(), f, default_flow_style=False, sort_keys=False)
