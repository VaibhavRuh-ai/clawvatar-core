"""Avatar store — manages avatar files, metadata, and per-agent assignments."""

from __future__ import annotations

import json
import logging
import shutil
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)


class AvatarStore:
    """Manages VRM/GLB avatar files and agent assignments."""

    def __init__(self, base_dir: str = "~/.clawvatar/avatars"):
        self.base_dir = Path(base_dir).expanduser()
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._registry_path = self.base_dir / "registry.json"
        self._registry = self._load_registry()

    def _load_registry(self) -> dict:
        if self._registry_path.exists():
            return json.loads(self._registry_path.read_text())
        return {"avatars": {}, "assignments": {}, "defaults": {"avatar_id": ""}}

    def _save_registry(self) -> None:
        self._registry_path.write_text(json.dumps(self._registry, indent=2))

    def add(self, file_path: str, name: str = "", metadata: dict | None = None) -> str:
        """Add an avatar file to the store. Returns avatar_id."""
        src = Path(file_path)
        if not src.exists():
            raise FileNotFoundError(f"Avatar file not found: {file_path}")

        avatar_id = f"av_{uuid.uuid4().hex[:8]}"
        dest = self.base_dir / f"{avatar_id}{src.suffix}"
        shutil.copy2(src, dest)

        self._registry["avatars"][avatar_id] = {
            "name": name or src.stem,
            "path": str(dest),
            "format": src.suffix.lstrip("."),
            "metadata": metadata or {},
        }
        self._save_registry()
        logger.info(f"Avatar added: {avatar_id} ({name or src.stem})")
        return avatar_id

    def get(self, avatar_id: str) -> dict | None:
        """Get avatar info by ID."""
        return self._registry["avatars"].get(avatar_id)

    def get_path(self, avatar_id: str) -> str | None:
        """Get avatar file path by ID."""
        info = self.get(avatar_id)
        return info["path"] if info else None

    def list(self) -> list[dict]:
        """List all avatars."""
        result = []
        for aid, info in self._registry["avatars"].items():
            result.append({"id": aid, **info})
        return result

    def delete(self, avatar_id: str) -> None:
        """Remove an avatar from the store."""
        info = self._registry["avatars"].pop(avatar_id, None)
        if info:
            Path(info["path"]).unlink(missing_ok=True)
            # Remove any assignments pointing to this avatar
            self._registry["assignments"] = {
                k: v for k, v in self._registry["assignments"].items() if v != avatar_id
            }
            self._save_registry()

    def assign(self, agent_id: str, avatar_id: str) -> None:
        """Assign an avatar to an agent."""
        if avatar_id not in self._registry["avatars"]:
            raise ValueError(f"Avatar not found: {avatar_id}")
        self._registry["assignments"][agent_id] = avatar_id
        self._save_registry()
        logger.info(f"Assigned avatar {avatar_id} to agent {agent_id}")

    def get_for_agent(self, agent_id: str) -> dict | None:
        """Get the assigned avatar for an agent."""
        avatar_id = self._registry["assignments"].get(agent_id)
        if not avatar_id:
            avatar_id = self._registry["defaults"].get("avatar_id", "")
        if avatar_id:
            info = self.get(avatar_id)
            if info:
                return {"id": avatar_id, **info}
        return None

    def set_default(self, avatar_id: str) -> None:
        """Set the default avatar for agents without assignment."""
        self._registry["defaults"]["avatar_id"] = avatar_id
        self._save_registry()

    def get_avatar_path_for_agent(self, agent_id: str) -> str | None:
        """Convenience: get the file path for an agent's avatar."""
        info = self.get_for_agent(agent_id)
        return info["path"] if info else None
