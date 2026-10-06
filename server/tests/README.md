# server/tests

Tests for the [model server](../README.md)'s own code. They need no GPU, no vLLM and no network.

| File | What it covers |
|---|---|
| [`test_auth.py`](test_auth.py) | The API-key check: only `/health` and `/ping` are public; every other route, method and near-miss path needs the key; no key, no start |
| [`test_sampling.py`](test_sampling.py) | The penalties middleware: added only when a request to an OpenAI-style generation route leaves them out, the body's new length, other routes and methods untouched, and the key checked first |
| [`test_launch.py`](test_launch.py) | The launcher: defaults, settings and their ranges, the `vllm serve` command, vLLM's environment without the token, the model folder check, the system prompt file, and the chat template: the training format, with the default system prompt first unless a request brings its own |
| [`test_smoke_test.py`](test_smoke_test.py) | The smoke test's requests: its own User-Agent, both address forms, and the routes vLLM leaves open |
| [`conftest.py`](conftest.py) | Puts `server/` on the import path, since its files are scripts, not a package |

## How they work

- **The key check** wraps a small app built in memory, and gets requests through a test client.
- **The launcher's functions** are called directly: nothing is downloaded, and vLLM never starts.
- **The smoke test's network call** is replaced by a fake.

## Run them

From the repository root:

```bash
pip install -r server/requirements-dev.txt
```

```bash
python -m pytest server/tests
```
