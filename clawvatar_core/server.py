"""Clawvatar Core server — dynamic, configurable, works with any OpenClaw system.

`pip install clawvatar-core && clawvatar-core serve`
Open browser → configure → use. No hardcoded anything.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Optional

import numpy as np
from fastapi import FastAPI, File, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from clawvatar_core import db
from clawvatar_core.adapters.openclaw import OpenClawAdapter

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
AVATAR_DIR = Path.home() / ".clawvatar" / "avatars"
AVATAR_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Clawvatar", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# Runtime state (not persisted)
_engine = None
_openclaw: Optional[OpenClawAdapter] = None


def _get_engine():
    global _engine
    if _engine is None:
        from clawvatar_core.engine.embedded import EmbeddedEngineClient
        from clawvatar_core.config import EngineConfig
        _engine = EmbeddedEngineClient(EngineConfig())
    return _engine


async def _get_openclaw() -> Optional[OpenClawAdapter]:
    global _openclaw
    if _openclaw and _openclaw.is_connected:
        return _openclaw

    url = db.get_setting("openclaw_url")
    token = db.get_setting("openclaw_token")
    if not url or not token:
        return None

    try:
        _openclaw = OpenClawAdapter(gateway_url=url, token=token)
        await _openclaw.connect()
        return _openclaw
    except Exception as e:
        logger.error(f"OpenClaw connect failed: {e}")
        return None


# ==================== UI ====================

@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/app.js")
async def app_js():
    return FileResponse(STATIC_DIR / "app.js", media_type="application/javascript")


# ==================== Settings ====================

@app.get("/api/settings")
async def get_settings():
    s = db.get_all_settings()
    # Mask secrets
    masked = {}
    for k, v in s.items():
        if "secret" in k or "api_key" in k or "token" in k:
            masked[k] = v[:8] + "..." if len(v) > 8 else "***"
        else:
            masked[k] = v
    return {"settings": masked, "configured": db.is_configured()}


@app.post("/api/settings")
async def save_settings(body: dict):
    for k, v in body.items():
        if v:  # don't save empty values
            db.set_setting(k, str(v))

    # Set env vars for LiveKit/LLM
    if body.get("livekit_url"):
        os.environ["LIVEKIT_URL"] = body["livekit_url"]
    if body.get("livekit_api_key"):
        os.environ["LIVEKIT_API_KEY"] = body["livekit_api_key"]
    if body.get("livekit_api_secret"):
        os.environ["LIVEKIT_API_SECRET"] = body["livekit_api_secret"]
    if body.get("google_api_key"):
        os.environ["GOOGLE_API_KEY"] = body["google_api_key"]
    if body.get("openai_api_key"):
        os.environ["OPENAI_API_KEY"] = body["openai_api_key"]

    return {"ok": True, "configured": db.is_configured()}


# ==================== OpenClaw ====================

@app.post("/api/openclaw/connect")
async def openclaw_connect(body: dict = {}):
    """Connect to OpenClaw and sync agents."""
    url = body.get("url") or db.get_setting("openclaw_url")
    token = body.get("token") or db.get_setting("openclaw_token")

    if not url or not token:
        return {"error": "OpenClaw URL and token required"}

    # Save to DB
    db.set_setting("openclaw_url", url)
    db.set_setting("openclaw_token", token)

    try:
        oc = await _get_openclaw()
        if not oc:
            return {"error": "Failed to connect"}

        agents = await oc.list_agents()
        openclaw_base = body.get("openclaw_base", db.get_setting("openclaw_base", os.path.expanduser("~/.openclaw")))
        db.set_setting("openclaw_base", openclaw_base)
        db.sync_openclaw_agents(agents, openclaw_base)

        return {"ok": True, "agents": len(agents)}
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/openclaw/status")
async def openclaw_status():
    connected = _openclaw is not None and _openclaw.is_connected
    return {"connected": connected, "url": db.get_setting("openclaw_url")}


# ==================== Agents ====================

@app.get("/api/agents")
async def list_agents_api():
    return {"agents": db.list_agents()}


@app.get("/api/agents/{agent_id}")
async def get_agent_api(agent_id: str):
    agent = db.get_agent(agent_id)
    if not agent:
        return {"error": "Agent not found"}
    return {"agent": agent}


@app.put("/api/agents/{agent_id}")
async def update_agent_api(agent_id: str, body: dict):
    existing = db.get_agent(agent_id)
    if not existing:
        return {"error": "Agent not found"}

    db.save_agent(
        agent_id=agent_id,
        name=body.get("name", existing.get("name", "")),
        avatar_id=body.get("avatar_id", existing.get("avatar_id", "")),
        soul_md=existing.get("soul_md", ""),
        instructions_override=body.get("instructions_override", existing.get("instructions_override", "")),
        provider=body.get("provider", existing.get("provider", "")),
        voice=body.get("voice", existing.get("voice", "")),
        model=body.get("model", existing.get("model", "")),
        openclaw_agent_id=existing.get("openclaw_agent_id", ""),
    )
    return {"ok": True}


@app.post("/api/agents/{agent_id}/assign-avatar/{avatar_id}")
async def assign_avatar_api(agent_id: str, avatar_id: str):
    db.assign_avatar(agent_id, avatar_id)
    return {"ok": True}


# ==================== Avatars ====================

@app.get("/api/avatars")
async def list_avatars_api():
    return {"avatars": db.list_avatars()}


@app.post("/api/avatars/upload")
async def upload_avatar(file: UploadFile = File(...)):
    ext = Path(file.filename).suffix.lower()
    if ext not in (".vrm", ".glb"):
        return {"error": "Only .vrm and .glb files"}

    avatar_id = f"av_{uuid.uuid4().hex[:8]}"
    save_path = AVATAR_DIR / f"{avatar_id}{ext}"
    content = await file.read()
    save_path.write_bytes(content)

    name = Path(file.filename).stem
    db.add_avatar(avatar_id, name, str(save_path), ext.lstrip("."))

    return {"avatar_id": avatar_id, "name": name, "path": str(save_path)}


@app.delete("/api/avatars/{avatar_id}")
async def delete_avatar_api(avatar_id: str):
    info = db.get_avatar(avatar_id)
    if info:
        Path(info["file_path"]).unlink(missing_ok=True)
    db.delete_avatar(avatar_id)
    return {"ok": True}


# ==================== LiveKit Token ====================

@app.get("/api/token")
async def get_token(agent_id: str = "", room: str = ""):
    from clawvatar_core.agent.room_manager import generate_token, create_room_name

    # Set env vars from DB
    for key in ["LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"]:
        val = db.get_setting(key.lower())
        if val:
            os.environ[key] = val

    try:
        room_name = room or create_room_name(prefix=agent_id or "clawvatar")
        token, url = generate_token(room_name, identity=f"user-{int(time.time())}")
        return {"token": token, "url": url, "room": room_name}
    except Exception as e:
        return {"error": str(e)}


# ==================== Engine / Animation ====================

@app.post("/api/engine/load-avatar")
async def engine_load_avatar(body: dict):
    path = body.get("path", "")
    if not path:
        return {"error": "path required"}
    try:
        engine = _get_engine()
        if not engine.is_connected:
            await engine.connect()
        info = await engine.load_avatar(path)
        return {"ok": True, "info": info}
    except Exception as e:
        return {"error": str(e)}


@app.websocket("/ws/animation")
async def animation_ws(ws: WebSocket):
    """WebSocket for avatar animation — idle streaming + audio processing."""
    await ws.accept()
    engine = _get_engine()
    if not engine.is_connected:
        await engine.connect()

    last_audio = 0.0
    alive = True

    async def idle_loop():
        while alive:
            if time.time() - last_audio > 1.5:
                try:
                    w = await engine.get_idle()
                    if w:
                        await ws.send_json(w)
                except Exception:
                    break
            await asyncio.sleep(0.1)

    idle_task = asyncio.create_task(idle_loop())

    try:
        while True:
            raw = await ws.receive_text()
            msg = json.loads(raw)
            t = msg.get("type")

            if t == "ping":
                await ws.send_json({"type": "pong"})
            elif t == "audio":
                last_audio = time.time()
                pcm = base64.b64decode(msg.get("data", ""))
                audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
                w = await engine.process_audio(audio)
                if w:
                    await ws.send_json(w)
            elif t == "avatar.load":
                try:
                    info = await engine.load_avatar(msg.get("path", ""))
                    await ws.send_json({"type": "avatar.ready", "info": info})
                except Exception as e:
                    await ws.send_json({"type": "error", "message": str(e)})

    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error(f"Animation WS error: {e}")
    finally:
        alive = False
        idle_task.cancel()


# ==================== Chat (OpenClaw delegation) ====================

@app.websocket("/ws/chat")
async def chat_ws(ws: WebSocket):
    """WebSocket for OpenClaw chat — send messages to agents, get responses."""
    await ws.accept()

    try:
        while True:
            raw = await ws.receive_text()
            msg = json.loads(raw)
            t = msg.get("type")

            if t == "ping":
                await ws.send_json({"type": "pong"})

            elif t == "send":
                agent_id = msg.get("agent_id", "")
                message = msg.get("message", "")

                if not agent_id or not message:
                    await ws.send_json({"type": "error", "message": "agent_id and message required"})
                    continue

                oc = await _get_openclaw()
                if not oc:
                    await ws.send_json({"type": "error", "message": "OpenClaw not connected"})
                    continue

                try:
                    await ws.send_json({"type": "status", "message": f"Sending to {agent_id}..."})
                    result = await oc.send_to_agent(agent_id, message, timeout=30)
                    text = result.get("text", "")
                    error = result.get("error", "")

                    if error:
                        await ws.send_json({"type": "error", "message": error})
                    else:
                        await ws.send_json({"type": "response", "agent_id": agent_id, "text": text})
                except Exception as e:
                    await ws.send_json({"type": "error", "message": str(e)})

    except WebSocketDisconnect:
        pass


# ==================== Health ====================

@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "configured": db.is_configured(),
        "openclaw_connected": _openclaw is not None and _openclaw.is_connected,
        "engine_connected": _engine is not None and _engine.is_connected,
    }


def create_app():
    """Load settings from DB into env vars on startup."""
    @app.on_event("startup")
    async def startup():
        settings = db.get_all_settings()
        for key in ["livekit_url", "livekit_api_key", "livekit_api_secret", "google_api_key", "openai_api_key"]:
            val = settings.get(key, "")
            if val:
                os.environ[key.upper()] = val

        # Auto-connect to OpenClaw if configured
        if settings.get("openclaw_url") and settings.get("openclaw_token"):
            try:
                await _get_openclaw()
            except Exception as e:
                logger.warning(f"Auto-connect to OpenClaw failed: {e}")

        logger.info("Clawvatar Core started")

    return app


create_app()
