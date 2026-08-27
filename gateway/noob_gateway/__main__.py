"""Command-line entry point."""

from __future__ import annotations

import argparse

from aiohttp import web

from .app import create_app
from .config import load_config


SHUTDOWN_TIMEOUT_SECONDS = 2.0


def main() -> None:
    parser = argparse.ArgumentParser(description="N.O.O.B. HID and video gateway")
    parser.add_argument("--config", default="/etc/noob/noob.toml")
    args = parser.parse_args()
    config = load_config(args.config)
    app = create_app(config)
    web.run_app(
        app,
        host=config.server.host,
        port=config.server.port,
        access_log=None,
        shutdown_timeout=SHUTDOWN_TIMEOUT_SECONDS,
    )


if __name__ == "__main__":
    main()
