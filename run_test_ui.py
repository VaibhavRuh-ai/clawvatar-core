"""Run Core + Engine + Test UI together for browser testing.

Starts:
1. Embedded engine (processes audio)
2. WebSocket sink on port 8766 (sends weights to browser)
3. Engine's HTTP server on port 8765 (serves test UI + handles avatar uploads + audio batches)

Open: https://openclaw-vaibhav.tail72d21d.ts.net:8765
- Upload VRM avatar
- Connect (auto-connects to ws on same host)
- Upload audio file — avatar animates

The engine server handles audio processing directly.
Core's WebSocket sink runs alongside for programmatic access.
"""

import asyncio
import logging
import sys

sys.path.insert(0, "/home/vaibhav/clawvatar-core")
sys.path.insert(0, "/home/vaibhav/clawvatar-engine")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")


def main():
    """Start the engine server with test UI."""
    import uvicorn
    from clawvatar.config import ClawvatarConfig
    from clawvatar.server import create_app

    config = ClawvatarConfig()
    create_app(config)

    uvicorn.run(
        "clawvatar.server:app",
        host="0.0.0.0",
        port=8765,
        ssl_certfile="/home/vaibhav/openclaw-vaibhav.tail72d21d.ts.net.crt",
        ssl_keyfile="/tmp/ts-ssl.key",
        log_level="info",
    )


if __name__ == "__main__":
    main()
