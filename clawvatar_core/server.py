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
from clawvatar_core.director import IdleDirector
from clawvatar_core.stream import AvatarStreamer

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
AVATAR_DIR = Path.home() / ".clawvatar" / "avatars"
AVATAR_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Clawvatar", version="0.1.0")

# CORS — configurable via env. Default: allow all (development).
# Production: set CLAWVATAR_CORS_ORIGINS=https://yourdomain.com
_cors_origins = os.environ.get("CLAWVATAR_CORS_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
)

# Runtime state (not persisted)
_engine = None
_openclaw: Optional[OpenClawAdapter] = None
_streamer: Optional[AvatarStreamer] = None


# ==================== API Key Auth (optional) ====================

from fastapi import Depends, Header, HTTPException, Request

def _check_api_key(request: Request, x_api_key: Optional[str] = Header(None)):
    """Optional API key auth. Set CLAWVATAR_API_KEY env var to enable."""
    required_key = os.environ.get("CLAWVATAR_API_KEY", "")
    if not required_key:
        return  # auth disabled
    # Skip auth for static files, embed, stream, and health
    path = request.url.path
    if path in ("/", "/app.js", "/embed", "/stream", "/api/health") or path.startswith("/api/stream/hls/"):
        return
    if x_api_key != required_key:
        raise HTTPException(status_code=401, detail="Invalid API key")


def _get_engine():
    global _engine
    if _engine is None:
        try:
            from clawvatar_core.engine.embedded import EmbeddedEngineClient
            from clawvatar_core.config import EngineConfig
            _engine = EmbeddedEngineClient(EngineConfig())
        except Exception as e:
            logger.error(f"Engine init failed: {e}")
            raise
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
async def save_settings(body: dict, _=Depends(_check_api_key)):
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
async def openclaw_connect(body: dict = {}, _=Depends(_check_api_key)):
    """Connect to OpenClaw gateway."""
    url = body.get("url") or db.get_setting("openclaw_url")
    token = body.get("token") or db.get_setting("openclaw_token")

    if not url or not token:
        return {"error": "OpenClaw URL and token required"}

    db.set_setting("openclaw_url", url)
    db.set_setting("openclaw_token", token)
    if body.get("openclaw_base"):
        db.set_setting("openclaw_base", body["openclaw_base"])

    try:
        global _openclaw
        _openclaw = None  # force reconnect
        oc = await _get_openclaw()
        if not oc:
            return {"error": "Failed to connect"}
        agents = await oc.list_agents()
        return {"ok": True, "agents_count": len(agents)}
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/openclaw/status")
async def openclaw_status():
    connected = _openclaw is not None and _openclaw.is_connected
    return {"connected": connected, "url": db.get_setting("openclaw_url")}


# ==================== Agents (live from OpenClaw) ====================

@app.get("/api/agents")
async def list_agents_api():
    """Fetch agents LIVE from OpenClaw, merge with avatar assignments."""
    oc = await _get_openclaw()
    if not oc:
        return {"agents": [], "error": "OpenClaw not connected"}

    try:
        oc_agents = await oc.list_agents()
    except Exception as e:
        return {"agents": [], "error": str(e)}

    assignments = db.get_all_assignments()
    agents = []
    for a in oc_agents:
        aid = a.get("id", "")
        avatar = assignments.get(aid)
        soul = db.read_soul_md(aid)
        agents.append({
            "id": aid,
            "model": a.get("model", ""),
            "status": a.get("status", ""),
            "avatar_id": avatar["avatar_id"] if avatar else None,
            "avatar_name": avatar["name"] if avatar else None,
            "avatar_path": avatar["file_path"] if avatar else None,
            "has_soul": bool(soul),
        })

    return {"agents": agents}


@app.post("/api/agents/{agent_id}/assign-avatar")
async def assign_avatar_api(agent_id: str, body: dict):
    avatar_id = body.get("avatar_id", "")
    if not avatar_id:
        db.unassign_avatar(agent_id)
        return {"ok": True, "action": "unassigned"}
    # Validate avatar exists
    if not db.get_avatar(avatar_id):
        return JSONResponse({"error": f"Avatar {avatar_id} not found"}, status_code=404)
    db.assign_avatar(agent_id, avatar_id)
    return {"ok": True, "action": "assigned"}


@app.get("/api/agents/{agent_id}/soul")
async def get_soul_api(agent_id: str):
    """Get SOUL.md content for an agent."""
    soul = db.read_soul_md(agent_id)
    return {"agent_id": agent_id, "soul_md": soul, "has_soul": bool(soul)}


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


@app.get("/api/avatars/{avatar_id}/file")
async def serve_avatar_file(avatar_id: str):
    """Serve avatar file for Three.js loading."""
    info = db.get_avatar(avatar_id)
    if not info or not Path(info["file_path"]).exists():
        return JSONResponse({"error": "Avatar file not found"}, status_code=404)
    return FileResponse(info["file_path"], media_type="application/octet-stream")


@app.get("/api/agents/{agent_id}/avatar")
async def get_agent_avatar_api(agent_id: str):
    """Get the avatar assigned to an agent."""
    info = db.get_agent_avatar(agent_id)
    if not info:
        return {"agent_id": agent_id, "avatar_id": None}
    return info


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
        _metrics["calls_total"] += 1
        _metrics["calls_active"] += 1
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
    _metrics["ws_connections"] += 1
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
        _metrics["ws_connections"] = max(0, _metrics["ws_connections"] - 1)


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

_director_last_call: dict[str, float] = {}  # agent_id → timestamp
_director_min_interval = 3.0  # seconds between calls per agent
_group_calls: dict[str, "GroupCall"] = {}  # room_name → GroupCall

@app.post("/api/director/action")
async def director_action(body: dict):
    """Get an idle action from the director LLM (fire-and-forget from client).

    Body: {agent_id, transcript, idle_seconds, last_action}
    Returns: {look, gesture, expression, move_to, duration}
    Rate-limited to 1 call per 3 seconds per agent.
    """
    agent_id = body.get("agent_id", "default")

    # Rate limit per agent
    now = time.time()
    last = _director_last_call.get(agent_id, 0)
    if now - last < _director_min_interval:
        return {"look": "user", "gesture": "none", "expression": "neutral",
                "move_to": "home", "duration": 4, "_rate_limited": True}
    _director_last_call[agent_id] = now
    _metrics["director_calls"] += 1

    try:
        api_key = db.get_setting("google_api_key")
        director = IdleDirector(api_key=api_key)
        action = await director.pick_action(
            agent_id=agent_id,
            transcript=body.get("transcript", ""),
            idle_seconds=float(body.get("idle_seconds", 5)),
            last_action=body.get("last_action", ""),
        )
        return action
    except Exception as e:
        logger.warning(f"Director error: {e}")
        _metrics["director_errors"] += 1
        return {"look": "user", "gesture": "none", "expression": "neutral",
                "move_to": "home", "duration": 4}


# ==================== Group Calls ====================

@app.post("/api/call/group")
async def start_group_call(body: dict):
    """Start a group call with multiple agents in one room.

    Body: {agents: ["system-architect", "vp-developer", ...]}
    Returns: {room, token, url, agents: [{id, identity, connected}]}
    """
    agent_ids = body.get("agents", [])
    if not agent_ids:
        return {"error": "No agents specified"}

    from clawvatar_core.agent.room_manager import generate_token, create_room_name
    from clawvatar_core.agent.group import GroupCall

    # Set env vars from DB
    for key in ["LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET", "GOOGLE_API_KEY"]:
        val = db.get_setting(key.lower())
        if val:
            os.environ[key] = val

    room_name = create_room_name(prefix="group")
    user_token, url = generate_token(room_name, identity=f"user-{int(time.time())}")

    # Create group call and add agents
    gc = GroupCall(room_name)
    for aid in agent_ids:
        await gc.add_agent(aid)

    _group_calls[room_name] = gc
    _metrics["calls_total"] += 1
    _metrics["calls_active"] += 1

    # Start agents in background (don't block the response)
    async def _start_agents():
        try:
            await gc.start()
            logger.info(f"Group call started: {room_name} with {len(agent_ids)} agents")
        except Exception as e:
            logger.error(f"Group call start failed: {e}")

    asyncio.create_task(_start_agents())

    return {
        "ok": True,
        "room": room_name,
        "token": user_token,
        "url": url,
        "agents": [{"id": aid} for aid in agent_ids],
    }


@app.post("/api/call/group/{room_name}/add")
async def group_add_agent(room_name: str, body: dict):
    """Add an agent to an existing group call."""
    gc = _group_calls.get(room_name)
    if not gc:
        return {"error": "Group call not found"}
    agent_id = body.get("agent_id", "")
    if not agent_id:
        return {"error": "agent_id required"}
    await gc.add_agent(agent_id)
    return {"ok": True, "status": gc.get_status()}


@app.post("/api/call/group/{room_name}/remove")
async def group_remove_agent(room_name: str, body: dict):
    """Remove an agent from a group call."""
    gc = _group_calls.get(room_name)
    if not gc:
        return {"error": "Group call not found"}
    agent_id = body.get("agent_id", "")
    if not agent_id:
        return {"error": "agent_id required"}
    await gc.remove_agent(agent_id)
    return {"ok": True, "status": gc.get_status()}


@app.post("/api/call/group/{room_name}/end")
async def group_end_call(room_name: str):
    """End a group call — all agents leave."""
    gc = _group_calls.pop(room_name, None)
    if not gc:
        return {"error": "Group call not found"}
    await gc.stop()
    _metrics["calls_active"] = max(0, _metrics["calls_active"] - 1)
    return {"ok": True}


@app.get("/api/call/group/{room_name}/status")
async def group_call_status(room_name: str):
    """Get group call status."""
    gc = _group_calls.get(room_name)
    if not gc:
        return {"error": "Group call not found"}
    return gc.get_status()


# ==================== Streaming ====================

@app.post("/api/stream/start")
async def stream_start(body: dict, _=Depends(_check_api_key)):
    """Start headless avatar stream.

    Body: {agent_id, room?, width?, height?, fps?,
           outputs: {hls?, rtmp?, file?, v4l2?}}
    """
    global _streamer
    if _streamer and _streamer.is_streaming:
        return {"error": "Already streaming — stop first", "status": _streamer.get_status()}

    agent_id = body.get("agent_id", "")
    room = body.get("room", "")
    width = int(body.get("width", 1280))
    height = int(body.get("height", 720))
    fps = int(body.get("fps", 30))
    outputs = body.get("outputs", {"hls": True})

    # Build server URL — check if SSL cert was passed to the serve command
    port = int(os.environ.get("CLAWVATAR_PORT", "8766"))
    # Detect SSL from the uvicorn config (ssl_keyfile is set in cli.py)
    has_ssl = bool(os.environ.get("SSL_CERT", "")) or any(
        "ssl" in str(getattr(app, k, "")) for k in dir(app)
    )
    protocol = "https"  # default to https since we always use SSL cert
    server_url = f"{protocol}://localhost:{port}"

    _streamer = AvatarStreamer(
        server_url=server_url,
        width=width,
        height=height,
        fps=fps,
    )

    try:
        await _streamer.start(agent_id=agent_id, room=room, outputs=outputs)
        return {"ok": True, "status": _streamer.get_status()}
    except Exception as e:
        return {"error": str(e)}


@app.post("/api/stream/stop")
async def stream_stop():
    """Stop active stream."""
    global _streamer
    if not _streamer:
        return {"error": "No active stream"}
    await _streamer.stop()
    return {"ok": True, "status": _streamer.get_status()}


@app.get("/api/stream/status")
async def stream_status():
    """Get current stream status."""
    if not _streamer:
        return {"status": "stopped"}
    return _streamer.get_status()


@app.get("/api/stream/hls/{filename:path}")
async def stream_hls(filename: str):
    """Serve HLS manifest and segments."""
    if not _streamer or not _streamer.hls_dir:
        return JSONResponse({"error": "No HLS stream active"}, status_code=404)

    filepath = Path(_streamer.hls_dir) / filename
    if not filepath.exists():
        return JSONResponse({"error": "Segment not found"}, status_code=404)

    if filename.endswith(".m3u8"):
        return FileResponse(filepath, media_type="application/vnd.apple.mpegurl",
                           headers={"Cache-Control": "no-cache", "Access-Control-Allow-Origin": "*"})
    elif filename.endswith(".ts"):
        return FileResponse(filepath, media_type="video/mp2t",
                           headers={"Access-Control-Allow-Origin": "*"})
    return FileResponse(filepath)


@app.get("/stream")
async def stream_view():
    """Clean stream view page — fullscreen avatar, no UI chrome.
    Used for OBS browser source, tab sharing, or headless capture."""
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/embed")
async def embed_view():
    """Compact embed widget — iframe-friendly avatar with mic button.
    Usage: <iframe src="https://host/embed?agent_id=X" allow="microphone"></iframe>"""
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "configured": db.is_configured(),
        "openclaw_connected": _openclaw is not None and _openclaw.is_connected,
        "engine_connected": _engine is not None and _engine.is_connected,
        "streaming": _streamer is not None and _streamer.is_streaming,
    }


# ==================== Metrics ====================

_metrics = {
    "server_start": time.time(),
    "calls_total": 0,
    "calls_active": 0,
    "director_calls": 0,
    "director_errors": 0,
    "avatars_loaded": 0,
    "ws_connections": 0,
}


@app.get("/api/metrics")
async def metrics():
    """Detailed system metrics for monitoring dashboard."""
    uptime = int(time.time() - _metrics["server_start"])
    agents_count = 0
    avatars_count = 0
    try:
        avatars_count = len(db.list_avatars())
        if _openclaw and _openclaw.is_connected:
            agents_count = len(await _openclaw.list_agents())
    except Exception:
        pass

    return {
        "uptime": uptime,
        "uptime_human": f"{uptime//3600}h {(uptime%3600)//60}m {uptime%60}s",
        "connections": {
            "openclaw": _openclaw is not None and _openclaw.is_connected,
            "engine": _engine is not None and _engine.is_connected,
            "streaming": _streamer is not None and _streamer.is_streaming,
        },
        "counts": {
            "agents": agents_count,
            "avatars": avatars_count,
            "calls_total": _metrics["calls_total"],
            "calls_active": _metrics["calls_active"],
            "ws_connections": _metrics["ws_connections"],
        },
        "director": {
            "calls": _metrics["director_calls"],
            "errors": _metrics["director_errors"],
            "model": "gemini-2.5-flash-lite",
        },
        "stream": _streamer.get_status() if _streamer else {"status": "stopped"},
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
