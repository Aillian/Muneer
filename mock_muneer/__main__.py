"""Run the mock Muneer server.

    python -m mock_muneer                 # http://127.0.0.1:8000, settings from .env
    python -m mock_muneer --port 8001

Reads MUNEER_API_KEY (the key clients must send), MUNEER_MODEL (default
"muneer"), MOCK_DELAY_MS (a pause before each reply), MOCK_HOST and MOCK_PORT
from the environment or from .env. --host and --port win over both.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import uvicorn

from mock_muneer.app import create_app


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Scripted stand-in for Muneer's OpenAI-compatible API.")
    parser.add_argument("--host", help="default: MOCK_HOST, or 127.0.0.1")
    parser.add_argument("--port", type=int, help="default: MOCK_PORT, or 8000")
    parser.add_argument("--env-file", type=Path, default=Path(".env"), help="default: .env in the current folder")
    args = parser.parse_args(argv)

    # The file is read before any setting, so MOCK_HOST and MOCK_PORT work there too.
    if args.env_file.is_file():
        from dotenv import load_dotenv

        load_dotenv(args.env_file, override=False)

    app = create_app(
        api_key=os.environ.get("MUNEER_API_KEY") or None,
        delay_ms=int(os.environ.get("MOCK_DELAY_MS") or 0),
        model=os.environ.get("MUNEER_MODEL") or "muneer",
    )
    host = args.host or os.environ.get("MOCK_HOST") or "127.0.0.1"
    port = args.port if args.port is not None else int(os.environ.get("MOCK_PORT") or 8000)
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
