"""Test doubles: no test ever calls the real OpenAI API."""

from __future__ import annotations

import json

import httpx
from openai import OpenAI


class FakeOpenAI:
    """An OpenAI-compatible server that plays back scripted outcomes, one per call.

    Each outcome is one of:
    - a dict, returned as the model's JSON answer;
    - a string, returned as the model's text ("refusal" makes it refuse);
    - an HTTP status code, or a (status, message, param) tuple for an API error;
    - an exception to raise (a network failure).

    `requests` keeps every request body the client sent.
    """

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.requests: list[dict] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(json.loads(request.content))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        if isinstance(outcome, int):
            return httpx.Response(outcome, json={"error": {"message": "scripted failure", "type": "server_error"}})
        if isinstance(outcome, tuple):
            status, text, param = outcome
            error = {"message": text, "type": "invalid_request_error", "param": param, "code": None}
            return httpx.Response(status, json={"error": error})
        message = {"role": "assistant", "content": None, "refusal": None}
        if outcome == "refusal":
            message["refusal"] = "I can't help with that."
        elif isinstance(outcome, str):
            message["content"] = outcome
        else:
            message["content"] = json.dumps(outcome)
        return httpx.Response(200, json={
            "id": "chatcmpl-test", "object": "chat.completion", "created": 0, "model": "gpt-4.1-2025-04-14",
            "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1234, "completion_tokens": 56, "total_tokens": 1290},
        })

    def client(self) -> OpenAI:
        transport = httpx.MockTransport(self.handler)
        return OpenAI(api_key="test", base_url="http://fake/v1", max_retries=0, http_client=httpx.Client(transport=transport))

    def user_data(self, call: int = 0) -> dict:
        """The JSON data sent in the user message of a call."""
        return json.loads(self.requests[call]["messages"][1]["content"].split("\n\n", 1)[1])
