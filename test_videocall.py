"""Full Video Call: LiveKit Agent + Clawvatar Avatar + OpenClaw.

The server:
1. Serves the frontend UI
2. Generates LiveKit tokens for browser to join rooms
3. Runs the engine for animation processing (audio → weights)
4. Streams idle animation weights via WebSocket

The LiveKit agent (separate process) handles the actual voice conversation.
The browser joins the LiveKit room for voice, and connects to this server
for avatar animation.

Start agent:  clawvatar-core agent --provider openai
Start server: python test_videocall.py
Open browser: https://openclaw-vaibhav.tail72d21d.ts.net:8766
"""

import asyncio
import base64
import json
import logging
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, "/home/vaibhav/clawvatar-core")
sys.path.insert(0, "/home/vaibhav/clawvatar-engine")

# Set LiveKit env vars — configure via environment or .env file in this directory
# LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET
env_path = os.path.join(os.path.dirname(__file__), ".env")
if os.path.exists(env_path):
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k, v)

import numpy as np
import uvicorn
from fastapi import FastAPI, File, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from clawvatar_core.config import EngineConfig
from clawvatar_core.engine.embedded import EmbeddedEngineClient
from clawvatar_core.agent.room_manager import generate_token, create_room_name

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("videocall")

app = FastAPI(title="Clawvatar Video Call")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

AVATAR_DIR = Path.home() / ".clawvatar" / "avatars"
STATIC_DIR = Path(__file__).parent / "clawvatar_core" / "static"
engine = None


@app.on_event("startup")
async def startup():
    global engine
    engine = EmbeddedEngineClient(EngineConfig())
    await engine.connect()
    for name in ["Juanita.vrm", "juanita.vrm", "Cyberpal.vrm"]:
        p = AVATAR_DIR / name
        if p.exists():
            await engine.load_avatar(str(p))
            break
    logger.info("Server started")


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.post("/upload")
async def upload_avatar(file: UploadFile = File(...)):
    AVATAR_DIR.mkdir(parents=True, exist_ok=True)
    path = AVATAR_DIR / file.filename
    path.write_bytes(await file.read())
    if engine:
        await engine.load_avatar(str(path))
    return {"path": str(path), "name": file.filename}


@app.get("/token")
async def get_token(room: str = "", identity: str = "user"):
    """Generate a LiveKit token for the browser to join a room."""
    try:
        room_name = room or create_room_name()
        token, lk_url = generate_token(room_name, identity)
        return {
            "token": token,
            "url": lk_url,
            "room": room_name,
        }
    except Exception as e:
        return {"error": str(e)}


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    """WebSocket for animation weights — idle streaming + audio batch processing."""
    await ws.accept()
    logger.info("Client connected")

    last_audio_time = 0.0
    disconnected = False

    async def idle_loop():
        while not disconnected:
            if time.time() - last_audio_time > 1.5:
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

            elif t == "avatar.load":
                try:
                    info = await engine.load_avatar(msg.get("model_path", ""))
                    await ws.send_json({"type": "avatar.ready", "info": info})
                except Exception as e:
                    await ws.send_json({"type": "error", "message": str(e)})

            elif t == "audio.batch":
                last_audio_time = time.time() + 9999
                try:
                    pcm = base64.b64decode(msg.get("data", ""))
                    sr = msg.get("sample_rate", 16000)
                    audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
                    result = await engine.process_batch(audio, sample_rate=sr)
                    frames = result.get("frames", [])
                    await ws.send_json({
                        "type": "batch_weights",
                        "frames": [{"w":f.get("w",{}),"h":f.get("h",{}),"v":f.get("v","REST"),"s":f.get("s",False)} for f in frames],
                        "duration": result.get("duration", 0),
                    })
                except Exception as e:
                    await ws.send_json({"type": "error", "message": str(e)})
                finally:
                    last_audio_time = time.time()

            elif t == "audio":
                last_audio_time = time.time()
                try:
                    pcm = base64.b64decode(msg.get("data", ""))
                    audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
                    w = await engine.process_audio(audio)
                    if w:
                        await ws.send_json(w)
                except Exception as e:
                    pass

    except WebSocketDisconnect:
        logger.info("Client disconnected")
    except Exception as e:
        logger.error(f"WS error: {e}")
    finally:
        disconnected = True
        idle_task.cancel()


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8766,
        ssl_certfile="/home/vaibhav/openclaw-vaibhav.tail72d21d.ts.net.crt",
        ssl_keyfile="/tmp/ts-ssl.key", log_level="info")
