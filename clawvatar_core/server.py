"""HTTP/WebSocket server for clawvatar-core — avatar management + animation streaming."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from clawvatar_core.config import CoreConfig
from clawvatar_core.session_manager import SessionManager

STATIC_DIR = Path(__file__).parent / "static"

logger = logging.getLogger(__name__)

app = FastAPI(title="Clawvatar Core", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

_manager: Optional[SessionManager] = None


def create_app(config: CoreConfig) -> FastAPI:
    global _manager

    @app.on_event("startup")
    async def startup():
        global _manager
        _manager = SessionManager(config)
        logger.info("Clawvatar Core server started")

    @app.on_event("shutdown")
    async def shutdown():
        if _manager:
            await _manager.destroy_all()

    return app


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
async def health():
    return {"status": "ok", "version": "0.1.0", "sessions": len(_manager._sessions) if _manager else 0}


@app.get("/sessions")
async def list_sessions():
    if not _manager:
        return {"sessions": []}
    return {"sessions": _manager.list_sessions()}


@app.get("/avatars")
async def list_avatars():
    if not _manager:
        return {"avatars": []}
    return {"avatars": _manager.avatar_store.list()}


@app.post("/avatars/upload")
async def upload_avatar(file: UploadFile = File(...), name: str = ""):
    if not _manager:
        return {"error": "Server not initialized"}

    ext = Path(file.filename).suffix.lower()
    if ext not in (".vrm", ".glb", ".gltf"):
        return {"error": f"Unsupported format: {ext}"}

    tmp_path = Path("/tmp") / file.filename
    content = await file.read()
    tmp_path.write_bytes(content)

    avatar_id = _manager.avatar_store.add(str(tmp_path), name=name or file.filename)
    tmp_path.unlink(missing_ok=True)

    return {"avatar_id": avatar_id, "name": name or file.filename, "size": len(content)}


@app.post("/avatars/{avatar_id}/assign/{agent_id}")
async def assign_avatar(avatar_id: str, agent_id: str):
    if not _manager:
        return {"error": "Server not initialized"}
    try:
        _manager.avatar_store.assign(agent_id, avatar_id)
        return {"ok": True, "agent_id": agent_id, "avatar_id": avatar_id}
    except ValueError as e:
        return {"error": str(e)}


@app.post("/sessions/{agent_id}/speak")
async def speak(agent_id: str, request: dict):
    """Send audio to an agent's avatar session."""
    if not _manager:
        return {"error": "Server not initialized"}

    session = _manager.get_session(agent_id)
    if not session:
        return {"error": f"No session for agent: {agent_id}"}

    import base64
    audio_b64 = request.get("audio_b64", "")
    sample_rate = request.get("sample_rate", 16000)
    text = request.get("text", "")

    if not audio_b64:
        return {"error": "audio_b64 required"}

    audio_bytes = base64.b64decode(audio_b64)
    await session.speak(audio=audio_bytes, sample_rate=sample_rate, text=text)
    return {"ok": True}
