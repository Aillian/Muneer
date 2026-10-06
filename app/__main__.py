"""Run the Muneer app:  python -m app  (from the repository root)

Reads .env, builds the pipeline and serves the Gradio app. Until the model
server is deployed, start the mock first:  python -m mock_muneer

Settings (environment variables), besides the pipeline's:
    APP_HOST               default 127.0.0.1 (use 0.0.0.0 inside Docker)
    APP_PORT               default 7860
    APP_CONCURRENCY        visitor messages processed at once, default 8
    ADMIN_PASSCODE         optional; opens the Conversations tab (every
                           stored conversation) and unlocks every
                           conversation's flagged replies in the review queue
                           tab. Without it, the Conversations tab stays locked
                           and each visitor sees only their own flagged replies
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
from pathlib import Path

from app.ui import CSS, THEME, build_ui
from pipeline.config import ConfigError, env_count, env_text
from pipeline.pipeline import Pipeline

MIN_PASSCODE = 16
RENAMED = {"REVIEW_QUEUE_PATH": "DATABASE_PATH", "REVIEW_QUEUE_PASSCODE": "ADMIN_PASSCODE"}


def admin_passcode(env) -> str | None:
    """ADMIN_PASSCODE, checked: it guards every stored conversation, and nothing limits guesses."""
    for old, new in RENAMED.items():
        if env_text(env, old):
            raise ConfigError(f"{old} is now called {new}; rename it in your settings.")
    passcode = env_text(env, "ADMIN_PASSCODE")
    if passcode and len(passcode) < MIN_PASSCODE:
        raise ConfigError(f"ADMIN_PASSCODE must be at least {MIN_PASSCODE} characters. Make one with: "
                          'python -c "import secrets; print(secrets.token_urlsafe(16))"')
    return passcode


def main() -> int:
    parser = argparse.ArgumentParser(description="Serve the Muneer chat app.")
    parser.add_argument("--env-file", type=Path, default=Path(".env"), help="default: .env in the current folder")
    args = parser.parse_args()
    if args.env_file.is_file():
        from dotenv import load_dotenv

        load_dotenv(args.env_file, override=False)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # one line per HTTP request is just noise

    try:
        pipeline = Pipeline.from_env(os.environ)
        concurrency = env_count(os.environ, "APP_CONCURRENCY", 8)
        port = env_count(os.environ, "APP_PORT", 7860)
        passcode = admin_passcode(os.environ)
    except ConfigError as err:
        print(f"error: {err}", file=sys.stderr)
        return 2

    ui = build_ui(pipeline, concurrency=concurrency, admin_passcode=passcode)
    ui.queue(default_concurrency_limit=concurrency)
    # `docker stop` sends SIGTERM; handle it like Ctrl+C so Gradio shuts down cleanly.
    signal.signal(signal.SIGTERM, signal.default_int_handler)
    ui.launch(
        server_name=env_text(os.environ, "APP_HOST", "127.0.0.1"),
        server_port=port,
        theme=THEME,
        css=CSS,
        show_error=False,  # the pipeline explains failures to the visitor itself
        footer_links=[],
        run_history=False,  # don't keep visitors' messages in the browser
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
