# Clawvatar Core

Integration bridge for [Clawvatar Engine](https://github.com/VaibhavRuh-ai/clawvatar-engine) — connects AI agents to animated 3D avatars.

Your agent speaks → Core sends audio to Engine → Engine returns animation → Core delivers to browser/virtual camera/video call.

## Install

```bash
pip install git+https://github.com/VaibhavRuh-ai/clawvatar-core
```

## Quick Start

### Standalone (simplest)

```python
import asyncio
from clawvatar_core.adapters.standalone import StandaloneAdapter

async def main():
    adapter = StandaloneAdapter(avatar_path="avatar.vrm")
    await adapter.start()

    # Your agent generates TTS audio
    audio_bytes = your_tts_engine("Hello! I'm your AI assistant.")

    # Avatar speaks with the audio
    await adapter.speak(audio_bytes, sample_rate=16000)

    await adapter.stop()

asyncio.run(main())
```

### With OpenClaw

```python
from clawvatar_core.adapters.openclaw import OpenClawAdapter

adapter = OpenClawAdapter(
    gateway_url="ws://localhost:18789",
    token="your-openclaw-token",
)
await adapter.start()

# When agent speaks:
await adapter.on_agent_speak("vp-manager", audio_bytes, sample_rate=16000)
```

### With Ruh Voice (LiveKit)

```python
from clawvatar_core.adapters.ruh_voice import RuhVoiceAdapter

adapter = RuhVoiceAdapter(
    livekit_url="wss://livekit.ruh.ai",
    api_key="...",
    api_secret="...",
)

# On call start:
await adapter.on_call_start(room_name, agent_id)

# On TTS audio chunk:
await adapter.on_tts_audio(room_name, agent_id, audio_chunk)

# On call end:
await adapter.on_call_end(room_name, agent_id)
```

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                   clawvatar-core                     │
│                                                      │
│  Adapters:          Session:          Sinks:         │
│  ┌──────────┐      ┌──────────┐     ┌────────────┐ │
│  │ OpenClaw │─────▶│  Avatar  │────▶│ WebSocket  │ │
│  │ RuhVoice │      │ Session  │     │ VirtualCam │ │
│  │Standalone│      │          │     │ LiveKit    │ │
│  └──────────┘      └────┬─────┘     │ File       │ │
│                         │           └────────────┘ │
│                    ┌────▼─────┐                     │
│                    │  Engine  │                     │
│                    │  Client  │                     │
│                    └────┬─────┘                     │
│                         │                           │
└─────────────────────────┼───────────────────────────┘
                          │
                 ┌────────▼────────┐
                 │ clawvatar-engine │
                 │ (audio → weights)│
                 └─────────────────┘
```

## Components

| Component | Purpose |
|---|---|
| **AvatarSession** | One per agent. Manages idle → speaking → idle lifecycle |
| **SessionManager** | Multi-agent registry. Creates/destroys sessions |
| **EngineClient** | Talks to clawvatar-engine (embedded or remote) |
| **AudioCollector** | Normalizes any audio format to PCM16 16kHz |
| **WebSocketSink** | Pushes animation to browser (Three.js viewer) |
| **AvatarStore** | Manages avatar files and per-agent assignments |
| **Adapters** | Platform-specific integration (OpenClaw, Ruh Voice, standalone) |

## CLI

```bash
# Start server
clawvatar-core serve

# Manage avatars
clawvatar-core avatars list
clawvatar-core avatars add avatar.vrm --name "Sara"
clawvatar-core avatars assign vp-manager av_abc123

# Init config
clawvatar-core init
```

## Configuration

```yaml
# clawvatar-core.yaml
engine:
  mode: embedded          # "embedded" (in-process) or "remote" (WebSocket)
  host: localhost          # remote engine host
  port: 8765              # remote engine port

avatar_store:
  base_dir: ~/.clawvatar/avatars
  default_avatar: ""

server:
  host: "0.0.0.0"
  port: 8766

idle_fps: 10
audio_buffer_ms: 200
```

## REST API

| Endpoint | Method | Purpose |
|---|---|---|
| `/health` | GET | Server status |
| `/sessions` | GET | List active sessions |
| `/avatars` | GET | List avatars |
| `/avatars/upload` | POST | Upload avatar file |
| `/avatars/{id}/assign/{agent}` | POST | Assign avatar to agent |
| `/sessions/{agent}/speak` | POST | Send audio to agent's avatar |

## License

Apache 2.0
