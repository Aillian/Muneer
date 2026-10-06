#!/usr/bin/env python3
"""Start Muneer's OpenAI-compatible API server on vLLM.

1. Read the settings from environment variables (and server/.env if present).
2. Download the weights from their private Hugging Face repo, unless
   MUNEER_MODEL_PATH points at a local copy.
3. Replace this process with `vllm serve`, configured for Muneer.

Usage:
    python server/launch.py                  # download if needed, then serve
    python server/launch.py --dry-run        # print the vLLM command and exit
    python server/launch.py --download-only  # fetch the weights and exit
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shlex
import shutil
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parent
CHAT_TEMPLATE = SERVER_DIR / "chat_template.jinja"
# Muneer's system prompt. The chat template adds it to every conversation that
# brings no system message of its own, so the app and API callers get it alike.
SYSTEM_PROMPT_FILE = SERVER_DIR / "system_prompt.md"
AUTH_MIDDLEWARE = "muneer_auth.ApiKeyMiddleware"
PENALTY_MIDDLEWARE = "muneer_sampling.PenaltyDefaultsMiddleware"

# Files vLLM needs from the model repo. Training artifacts such as
# trainer_state.json and training_args.bin are never downloaded.
MODEL_FILE_PATTERNS = [
    "config.json",
    "generation_config.json",
    "*.safetensors",
    "*.safetensors.index.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "special_tokens_map.json",
    "added_tokens.json",
    "vocab.json",
    "merges.txt",
    "chat_template.jinja",
]


class ConfigError(Exception):
    """The settings are missing or invalid."""


def default_download_dir() -> Path:
    # RunPod mounts its persistent volume at /workspace. Downloading there
    # means a pod restart does not fetch the ~30 GB of weights again.
    workspace = Path("/workspace")
    base = workspace if workspace.is_dir() else Path.home() / ".cache" / "muneer"
    return base / "models" / "muneer"


def _text(env: Mapping[str, str], name: str) -> str | None:
    value = env.get(name, "").strip()
    return value or None


def _number(env: Mapping[str, str], name: str, default, kind, low=None, high=None):
    raw = _text(env, name)
    if raw is None:
        return default
    try:
        value = kind(raw)
    except ValueError:
        raise ConfigError(f"{name} must be a {kind.__name__}, got {raw!r}") from None
    if not math.isfinite(value):  # "nan" passes any range check
        raise ConfigError(f"{name} must be a finite number, got {raw!r}")
    if (low is not None and value < low) or (high is not None and value > high):
        raise ConfigError(f"{name}={value} is outside the allowed range [{low}, {high}]")
    return value


@dataclass
class Settings:
    hf_repo: str | None = None
    hf_revision: str | None = None
    model_path: Path | None = None
    download_dir: Path = field(default_factory=default_download_dir)
    api_key: str | None = None
    served_model_name: str = "muneer"
    host: str = "0.0.0.0"
    port: int = 8000
    max_model_len: int = 8192
    gpu_memory_utilization: float = 0.92  # vLLM's own default
    tensor_parallel_size: int = 1
    max_num_seqs: int | None = None
    quantization: str | None = None
    temperature: float = 0.3
    top_p: float = 0.95
    top_k: int = 20
    repetition_penalty: float = 1.1
    frequency_penalty: float = 0.2
    presence_penalty: float = 0.0
    max_output_tokens: int = 1500
    system_prompt: str = ""
    extra_args: list[str] = field(default_factory=list)

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> Settings:
        d = cls()
        model_path = _text(env, "MUNEER_MODEL_PATH")
        download_dir = _text(env, "MUNEER_DOWNLOAD_DIR")
        prompt_file = Path(_text(env, "MUNEER_SYSTEM_PROMPT_FILE") or SYSTEM_PROMPT_FILE).expanduser()
        try:
            system_prompt = prompt_file.read_text(encoding="utf-8").strip()  # empty: no system prompt
        except OSError:
            raise ConfigError(f"MUNEER_SYSTEM_PROMPT_FILE: can't read the system prompt at {prompt_file}") from None
        settings = cls(
            hf_repo=_text(env, "MUNEER_HF_REPO"),
            hf_revision=_text(env, "MUNEER_HF_REVISION"),
            model_path=Path(model_path).expanduser() if model_path else None,
            download_dir=Path(download_dir).expanduser() if download_dir else d.download_dir,
            api_key=_text(env, "MUNEER_API_KEY"),
            served_model_name=_text(env, "MUNEER_SERVED_MODEL_NAME") or d.served_model_name,
            host=_text(env, "MUNEER_HOST") or d.host,
            port=_number(env, "MUNEER_PORT", d.port, int, 1, 65535),
            max_model_len=_number(env, "MUNEER_MAX_MODEL_LEN", d.max_model_len, int, 512),
            gpu_memory_utilization=_number(
                env, "MUNEER_GPU_MEMORY_UTILIZATION", d.gpu_memory_utilization, float, 0.1, 1.0
            ),
            tensor_parallel_size=_number(env, "MUNEER_TENSOR_PARALLEL_SIZE", d.tensor_parallel_size, int, 1),
            max_num_seqs=_number(env, "MUNEER_MAX_NUM_SEQS", None, int, 1),
            quantization=_text(env, "MUNEER_QUANTIZATION"),
            temperature=_number(env, "MUNEER_TEMPERATURE", d.temperature, float, 0.0, 2.0),
            top_p=_number(env, "MUNEER_TOP_P", d.top_p, float, 0.01, 1.0),
            top_k=_number(env, "MUNEER_TOP_K", d.top_k, int, -1),
            repetition_penalty=_number(env, "MUNEER_REPETITION_PENALTY", d.repetition_penalty, float, 0.01, 2.0),
            frequency_penalty=_number(env, "MUNEER_FREQUENCY_PENALTY", d.frequency_penalty, float, -2.0, 2.0),
            presence_penalty=_number(env, "MUNEER_PRESENCE_PENALTY", d.presence_penalty, float, -2.0, 2.0),
            max_output_tokens=_number(env, "MUNEER_MAX_OUTPUT_TOKENS", d.max_output_tokens, int, 16),
            system_prompt=system_prompt,
            extra_args=shlex.split(env.get("MUNEER_VLLM_EXTRA_ARGS", "")),
        )
        if not settings.hf_repo and not settings.model_path:
            raise ConfigError(
                "Set MUNEER_HF_REPO (the private Hugging Face repo with the weights) "
                "or MUNEER_MODEL_PATH (a local folder with the weights)."
            )
        return settings


def check_model_dir(path: Path) -> None:
    if not (path / "config.json").is_file():
        raise ConfigError(f"No config.json in {path}; is this the model folder?")
    if not any(path.glob("*.safetensors")):
        raise ConfigError(f"No .safetensors weights in {path}.")


def resolve_model(settings: Settings) -> Path:
    """Return a local folder with the weights, downloading them if needed."""
    if settings.model_path:
        path = settings.model_path
    else:
        from huggingface_hub import snapshot_download

        if not os.environ.get("HF_TOKEN"):
            print("warning: HF_TOKEN is not set; a private repo cannot be downloaded without it.", file=sys.stderr)
        revision = settings.hf_revision or "main"
        print(f"Fetching {settings.hf_repo}@{revision} into {settings.download_dir}", flush=True)
        # Resumable, and skips files that are already up to date.
        path = Path(
            snapshot_download(
                repo_id=settings.hf_repo,
                revision=settings.hf_revision,
                local_dir=settings.download_dir,
                allow_patterns=MODEL_FILE_PATTERNS,
            )
        )
    check_model_dir(path)
    return path


def build_vllm_command(settings: Settings, model_path: Path, auth: bool = True) -> list[str]:
    # Server-wide sampling defaults. Requests may override them, except
    # max_new_tokens, which vLLM applies as a hard cap on every reply. vLLM
    # takes no server-wide frequency or presence penalty, so muneer_sampling
    # adds those to each request (build_vllm_env passes it the values).
    generation = {
        "temperature": settings.temperature,
        "top_p": settings.top_p,
        "top_k": settings.top_k,
        "repetition_penalty": settings.repetition_penalty,
        "max_new_tokens": settings.max_output_tokens,
    }
    cmd = [
        "vllm", "serve", str(model_path),
        # Clients call the model "muneer"; the weights' location stays private.
        "--served-model-name", settings.served_model_name,
        "--host", settings.host,
        "--port", str(settings.port),
        "--dtype", "bfloat16",
        "--max-model-len", str(settings.max_model_len),
        "--gpu-memory-utilization", str(settings.gpu_memory_utilization),
        "--tensor-parallel-size", str(settings.tensor_parallel_size),
        # The plain ChatML format Muneer was trained on (no <think> blocks).
        "--chat-template", str(CHAT_TEMPLATE),
        "--generation-config", "auto",
        "--override-generation-config", json.dumps(generation),
        "--disable-access-log-for-endpoints", "/health,/ping",
        "--middleware", PENALTY_MIDDLEWARE,
    ]
    if settings.system_prompt:
        # The template uses it when a request brings no system message; a request's own wins.
        cmd += ["--default-chat-template-kwargs", json.dumps({"default_system_prompt": settings.system_prompt})]
    if auth:
        # vLLM wraps the app in each middleware in turn, so the last one runs
        # first: the key is checked before any request body is read.
        cmd += ["--middleware", AUTH_MIDDLEWARE]
    if settings.max_num_seqs:
        cmd += ["--max-num-seqs", str(settings.max_num_seqs)]
    if settings.quantization:
        cmd += ["--quantization", settings.quantization]
    return cmd + settings.extra_args


def build_vllm_env(base: Mapping[str, str], settings: Settings) -> dict[str, str]:
    env = dict(base)
    # vLLM only reads the local copy, so it never needs the Hugging Face token.
    env.pop("HF_TOKEN", None)
    env.pop("HUGGING_FACE_HUB_TOKEN", None)
    # Don't send vLLM's anonymous usage statistics.
    env.setdefault("VLLM_NO_USAGE_STATS", "1")
    env.setdefault("DO_NOT_TRACK", "1")
    # Lets vLLM import the middleware (muneer_auth.py and muneer_sampling.py sit next to this file).
    env["PYTHONPATH"] = os.pathsep.join(p for p in (str(SERVER_DIR), env.get("PYTHONPATH")) if p)
    # The penalties muneer_sampling adds to requests that leave them out.
    env["MUNEER_FREQUENCY_PENALTY"] = str(settings.frequency_penalty)
    env["MUNEER_PRESENCE_PENALTY"] = str(settings.presence_penalty)
    return env


def load_env_file(path: Path) -> None:
    if not path.is_file():
        return
    from dotenv import load_dotenv

    # Variables already set in the environment win over the file.
    load_dotenv(path, override=False)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Start Muneer's OpenAI-compatible vLLM server.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="print the vLLM command without downloading or serving")
    mode.add_argument("--download-only", action="store_true", help="download the weights and exit")
    parser.add_argument("--no-auth", action="store_true", help="serve without an API key (local testing only)")
    parser.add_argument("--env-file", type=Path, default=SERVER_DIR / ".env", help="default: server/.env")
    args = parser.parse_args(argv)

    load_env_file(args.env_file)
    serving = not (args.dry_run or args.download_only)
    try:
        settings = Settings.from_env(os.environ)
        if serving and not args.no_auth and not settings.api_key:
            raise ConfigError(
                "Set MUNEER_API_KEY; clients must send it as 'Authorization: Bearer <key>'. "
                "Use --no-auth only for local testing."
            )
        if args.dry_run:
            model_path = settings.model_path or settings.download_dir
        else:
            model_path = resolve_model(settings)
    except ConfigError as err:
        print(f"error: {err}", file=sys.stderr)
        return 2

    if args.download_only:
        print(f"Model ready at {model_path}")
        return 0

    cmd = build_vllm_command(settings, model_path, auth=not args.no_auth)
    if args.dry_run:
        print(shlex.join(cmd))
        return 0

    vllm = shutil.which("vllm")
    if not vllm:
        print("error: vLLM is not installed; run: pip install -r server/requirements.txt", file=sys.stderr)
        return 1
    if args.no_auth:
        print("warning: serving WITHOUT an API key; do not expose this port.", file=sys.stderr)
    print(f"Starting vLLM: {shlex.join(cmd)}", flush=True)
    # exec so vLLM receives the container's stop signals directly.
    os.execve(vllm, cmd, build_vllm_env(os.environ, settings))
    return 0  # not reached


if __name__ == "__main__":
    sys.exit(main())
