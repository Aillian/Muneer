#!/usr/bin/env python3
"""Check a running Muneer server end to end.

    python server/smoke_test.py --base-url https://<pod-id>-8000.proxy.runpod.net

It checks that /health is up, that requests without the key are refused,
that the model is listed, and that a chat completion works both whole and
streamed. The key comes from --api-key or MUNEER_API_KEY; without one, the
key checks are skipped (for a server started with --no-auth).

The address may end in /v1, as MUNEER_BASE_URL in .env does; either form works.

Needs only `pip install openai`, so it runs from any laptop.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

from openai import APIError, OpenAI

# Routes that must refuse a request with no key. /invocations, /tokenize and
# /metrics are outside vLLM's own --api-key check; muneer_auth.py closes them.
# vLLM's /metrics app answers any method, so OPTIONS is checked too.
GUARDED_ROUTES = [
    ("GET", "/v1/models"),
    ("POST", "/invocations"),
    ("POST", "/tokenize"),
    ("GET", "/metrics"),
    ("OPTIONS", "/metrics"),
]


def server_root(url: str) -> str:
    """The server's root address, whether or not `url` ends in /v1."""
    return url.rstrip("/").removesuffix("/v1")


# RunPod's proxy sits behind Cloudflare, which blocks urllib's default
# "Python-urllib" User-Agent (403, "error code: 1010") before the pod sees it.
USER_AGENT = "muneer-smoke-test/1.0"


def http_request(method: str, url: str, body: dict | None = None, timeout: float = 30) -> tuple[int, bytes]:
    """The HTTP status and the start of the body of a request without an API key.
    Uses only the standard library, so the smoke test needs nothing but openai."""
    data = json.dumps(body).encode() if body is not None else None
    headers = {"User-Agent": USER_AGENT}
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read(300)
    except urllib.error.HTTPError as err:
        return err.code, err.read(300)


class Report:
    def __init__(self) -> None:
        self.failures = 0

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        self.failures += not ok
        print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""), flush=True)
        return ok


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke-test a running Muneer server.")
    parser.add_argument("--base-url", default=os.environ.get("MUNEER_BASE_URL", "http://127.0.0.1:8000"),
                        help="the server's address, with or without a trailing /v1")
    parser.add_argument("--api-key", default=os.environ.get("MUNEER_API_KEY"))
    parser.add_argument("--model", default=os.environ.get("MUNEER_SERVED_MODEL_NAME", "muneer"))
    parser.add_argument("--prompt", default="What is Islam?")
    args = parser.parse_args()

    base = server_root(args.base_url)
    report = Report()

    try:
        status, body = http_request("GET", f"{base}/health")
    except (urllib.error.URLError, OSError) as err:
        report.check("server reachable", False, str(err))
        return 1
    detail = f"HTTP {status}"
    if status == 403 and b"1010" in body:
        detail += "; a proxy (Cloudflare) blocked the request before it reached the server"
    if not report.check("GET /health", status == 200, detail):
        return 1

    if args.api_key:
        probe = {"model": args.model, "messages": [{"role": "user", "content": "hi"}], "max_tokens": 1}
        for method, path in GUARDED_ROUTES:
            payload = probe if path == "/invocations" else {"model": args.model, "prompt": "hi"}
            status, _ = http_request(method, f"{base}{path}", payload if method == "POST" else None)
            report.check(f"{method} {path} without a key is refused", status == 401, f"HTTP {status}")
    else:
        print("[SKIP] key checks (no --api-key or MUNEER_API_KEY)")

    client = OpenAI(base_url=f"{base}/v1", api_key=args.api_key or "no-key", timeout=180)
    try:
        check_chat(client, args, report)
    except APIError as err:
        report.check("OpenAI API request", False, f"{type(err).__name__}: {err}")

    print(f"\n{'All checks passed.' if not report.failures else f'{report.failures} check(s) failed.'}")
    return 1 if report.failures else 0


def check_chat(client: OpenAI, args: argparse.Namespace, report: Report) -> None:
    models = [m.id for m in client.models.list().data]
    if not report.check(f"model '{args.model}' is listed", args.model in models, f"served: {models}"):
        return

    messages = [{"role": "user", "content": args.prompt}]

    start = time.perf_counter()
    reply = client.chat.completions.create(model=args.model, messages=messages)
    text = (reply.choices[0].message.content or "").strip()
    usage = reply.usage
    report.check(
        "chat completion",
        bool(text),
        f"{time.perf_counter() - start:.1f}s, {usage.completion_tokens if usage else '?'} tokens, "
        f"finish={reply.choices[0].finish_reason}",
    )
    print(f"\nQ: {args.prompt}\nA: {text}\n")

    start, first_token, parts = time.perf_counter(), None, []
    for chunk in client.chat.completions.create(model=args.model, messages=messages, stream=True):
        delta = chunk.choices[0].delta.content if chunk.choices else None
        if delta:
            first_token = first_token or time.perf_counter() - start
            parts.append(delta)
    report.check(
        "streamed chat completion",
        bool("".join(parts).strip()),
        f"first token {first_token or 0:.2f}s, total {time.perf_counter() - start:.1f}s",
    )


if __name__ == "__main__":
    sys.exit(main())
