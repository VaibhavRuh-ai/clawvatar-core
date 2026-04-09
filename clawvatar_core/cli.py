"""Clawvatar Core CLI.

Usage:
    clawvatar-core serve                    # Start the server
    clawvatar-core serve --port 8766        # Custom port
    clawvatar-core agent --provider google  # Start LiveKit agent worker
"""

from __future__ import annotations

import argparse
import logging
import os
import sys


def main():
    parser = argparse.ArgumentParser(prog="clawvatar-core", description="Clawvatar Core — AI agent avatar system")
    sub = parser.add_subparsers(dest="command")

    # serve
    s = sub.add_parser("serve", help="Start the web server")
    s.add_argument("--host", default="0.0.0.0")
    s.add_argument("--port", type=int, default=8766)
    s.add_argument("--ssl-cert", default="")
    s.add_argument("--ssl-key", default="")

    # agent
    a = sub.add_parser("agent", help="Start LiveKit agent worker")
    a.add_argument("--provider", default="google", choices=["openai", "google"])
    a.add_argument("--agent-id", default="", help="Agent ID to load SOUL.md from DB")

    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")

    # Load .env if present
    env_path = os.path.join(os.getcwd(), ".env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())

    if args.command == "serve":
        _serve(args)
    elif args.command == "agent":
        _agent(args)
    else:
        parser.print_help()
        sys.exit(1)


def _serve(args):
    import uvicorn
    kw = {"host": args.host, "port": args.port, "log_level": "info"}
    if args.ssl_cert and args.ssl_key:
        kw["ssl_certfile"] = args.ssl_cert
        kw["ssl_keyfile"] = args.ssl_key
    uvicorn.run("clawvatar_core.server:app", **kw)


def _agent(args):
    from clawvatar_core import db

    # Load settings from DB into env
    settings = db.get_all_settings()
    for key in ["livekit_url", "livekit_api_key", "livekit_api_secret", "google_api_key", "openai_api_key"]:
        val = settings.get(key, "")
        if val:
            os.environ.setdefault(key.upper(), val)

    # Get agent instructions from DB (SOUL.md)
    instructions = "You are a helpful AI assistant."
    if args.agent_id:
        agent = db.get_agent(args.agent_id)
        if agent:
            soul = agent.get("soul_md", "")
            override = agent.get("instructions_override", "")
            if override:
                instructions = override
            elif soul:
                # Use first 2000 chars of SOUL.md as instructions
                instructions = f"You are the {args.agent_id} agent. Follow your role:\n\n{soul[:2000]}"
            if agent.get("provider"):
                args.provider = agent["provider"]

    from clawvatar_core.agent.worker import ClawvatarAgentWorker
    worker = ClawvatarAgentWorker(
        provider=args.provider,
        instructions=instructions,
    )
    worker.run()


if __name__ == "__main__":
    main()
