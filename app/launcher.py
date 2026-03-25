from __future__ import annotations

import argparse
import os
import socket
import threading
import time
import webbrowser
from typing import Iterable, Optional

import uvicorn


def _browser_host_for_bind_host(host: str) -> str:
    value = str(host or "").strip()
    if value in {"0.0.0.0", "::", "[::]"}:
        return "127.0.0.1"
    return value or "127.0.0.1"


def _wait_for_tcp(host: str, port: int, timeout_seconds: float = 20.0) -> bool:
    deadline = time.monotonic() + max(0.1, float(timeout_seconds))
    while time.monotonic() < deadline:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(0.4)
        try:
            sock.connect((host, int(port)))
            sock.close()
            return True
        except OSError:
            time.sleep(0.15)
        finally:
            try:
                sock.close()
            except OSError:
                pass
    return False


def _open_browser_when_ready(bind_host: str, port: int) -> None:
    host = _browser_host_for_bind_host(bind_host)
    if _wait_for_tcp(host, port, timeout_seconds=20.0):
        webbrowser.open(f"http://{host}:{int(port)}", new=2)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the Domain Search web UI locally.")
    parser.add_argument("--host", default=os.getenv("DOMAIN_SEARCH_HOST", "127.0.0.1"), help="Bind host (default: 127.0.0.1).")
    parser.add_argument("--port", type=int, default=int(os.getenv("DOMAIN_SEARCH_PORT", "8000")), help="Bind port (default: 8000).")
    parser.add_argument("--open", action="store_true", help="Open the UI in your default browser after server starts.")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload for development.")
    parser.add_argument(
        "--log-level",
        default=os.getenv("DOMAIN_SEARCH_LOG_LEVEL", "info"),
        choices=("critical", "error", "warning", "info", "debug", "trace"),
        help="Uvicorn log level (default: info).",
    )
    return parser


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.port < 1 or args.port > 65535:
        parser.error("--port must be between 1 and 65535.")

    if args.open:
        threading.Thread(
            target=_open_browser_when_ready,
            args=(args.host, args.port),
            daemon=True,
        ).start()

    uvicorn.run(
        "app.main:app",
        host=args.host,
        port=args.port,
        reload=bool(args.reload),
        log_level=args.log_level,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
