"""CLI for clawvatar-core."""

from __future__ import annotations

import argparse
import logging
import sys


def main():
    parser = argparse.ArgumentParser(prog="clawvatar-core", description="Clawvatar Core — AI agent avatar integration")
    sub = parser.add_subparsers(dest="command")

    # serve
    s = sub.add_parser("serve", help="Start the core server")
    s.add_argument("-c", "--config", default="clawvatar-core.yaml")
    s.add_argument("--host", default=None)
    s.add_argument("--port", type=int, default=None)
    s.add_argument("--ssl-cert", default=None)
    s.add_argument("--ssl-key", default=None)

    # avatars
    a = sub.add_parser("avatars", help="Manage avatars")
    asub = a.add_subparsers(dest="avatar_cmd")
    asub.add_parser("list", help="List all avatars")
    aa = asub.add_parser("add", help="Add an avatar")
    aa.add_argument("file", help="VRM/GLB file path")
    aa.add_argument("--name", default="")
    ad = asub.add_parser("assign", help="Assign avatar to agent")
    ad.add_argument("agent_id")
    ad.add_argument("avatar_id")

    # agent
    ag = sub.add_parser("agent", help="Start LiveKit agent with avatar")
    ag.add_argument("--provider", default="openai", choices=["openai", "google"], help="Realtime LLM provider")
    ag.add_argument("--model", default="", help="Model name (auto-selects default)")
    ag.add_argument("--voice", default="", help="Voice name")
    ag.add_argument("--instructions", default="", help="System instructions for the agent")
    ag.add_argument("--avatar", default="", help="Path to VRM avatar file")
    ag.add_argument("--openclaw", action="store_true", help="Enable OpenClaw task delegation")
    ag.add_argument("--livekit-url", default="", help="LiveKit server URL")
    ag.add_argument("--api-key", default="", help="LiveKit API key")
    ag.add_argument("--api-secret", default="", help="LiveKit API secret")

    # init
    sub.add_parser("init", help="Create default config file")

    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")

    if args.command == "serve":
        _serve(args)
    elif args.command == "agent":
        _agent(args)
    elif args.command == "avatars":
        _avatars(args)
    elif args.command == "init":
        from clawvatar_core.config import CoreConfig
        CoreConfig().to_yaml("clawvatar-core.yaml")
        print("Config written to clawvatar-core.yaml")
    else:
        parser.print_help()
        sys.exit(1)


def _serve(args):
    import uvicorn
    from clawvatar_core.config import CoreConfig
    from clawvatar_core.server import create_app

    config = CoreConfig.from_yaml(args.config)
    if args.host:
        config.server.host = args.host
    if args.port:
        config.server.port = args.port
    if args.ssl_cert:
        config.server.ssl_cert = args.ssl_cert
    if args.ssl_key:
        config.server.ssl_key = args.ssl_key

    create_app(config)
    kw = {"host": config.server.host, "port": config.server.port, "log_level": "info"}
    if config.server.ssl_cert and config.server.ssl_key:
        kw["ssl_certfile"] = config.server.ssl_cert
        kw["ssl_keyfile"] = config.server.ssl_key
    uvicorn.run("clawvatar_core.server:app", **kw)


def _agent(args):
    from clawvatar_core.agent.worker import ClawvatarAgentWorker

    worker = ClawvatarAgentWorker(
        provider=args.provider,
        model=args.model,
        voice=args.voice,
        instructions=args.instructions or "You are a helpful AI assistant. Be concise and friendly.",
        avatar_path=args.avatar,
        openclaw_enabled=args.openclaw,
        livekit_url=args.livekit_url,
        livekit_api_key=args.api_key,
        livekit_api_secret=args.api_secret,
    )
    worker.run()


def _avatars(args):
    from clawvatar_core.avatar.store import AvatarStore
    store = AvatarStore()

    if args.avatar_cmd == "list":
        for a in store.list():
            print(f"  {a['id']}  {a['name']}  {a['path']}")
    elif args.avatar_cmd == "add":
        aid = store.add(args.file, name=args.name)
        print(f"Added: {aid}")
    elif args.avatar_cmd == "assign":
        store.assign(args.agent_id, args.avatar_id)
        print(f"Assigned {args.avatar_id} → {args.agent_id}")
    else:
        print("Use: clawvatar-core avatars list|add|assign")


if __name__ == "__main__":
    main()
