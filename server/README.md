# server

Serves Muneer, the fine-tuned model, with [vLLM](https://docs.vllm.ai) 0.29.0, behind an OpenAI-compatible API that
needs an API key. The pipeline sends it the conversation and gets back Muneer's raw reply. The weights are not in this
repository: the launcher downloads them from a private Hugging Face repo.

## Files

| File | What it does |
|---|---|
| [`launch.py`](launch.py) | Reads the settings, gets the weights, then replaces itself with `vllm serve` |
| [`muneer_auth.py`](muneer_auth.py) | `ApiKeyMiddleware`: the API-key check vLLM loads |
| [`muneer_sampling.py`](muneer_sampling.py) | `PenaltyDefaultsMiddleware`: adds the default frequency and presence penalties to chat requests |
| [`chat_template.jinja`](chat_template.jinja) | Plain ChatML, the format Muneer was [trained](../training/README.md) on, with the system prompt first |
| [`system_prompt.md`](system_prompt.md) | Muneer's system prompt |
| [`smoke_test.py`](smoke_test.py) | Checks a running server end to end, from any machine |
| [`.env.example`](.env.example) | Every setting, with comments. Copy it to `server/.env` |
| [`requirements.txt`](requirements.txt) | For the GPU machine: vLLM 0.29.0, `huggingface_hub`, `python-dotenv` |
| [`requirements-dev.txt`](requirements-dev.txt) | For the tests and the smoke test; no GPU or vLLM |
| [`tests/`](tests/README.md) | The launcher, the key check, the penalties middleware and the smoke test |

## What `launch.py` does

1. **Reads the settings** (`Settings.from_env`) from the environment, then `server/.env`. The environment wins.
2. **Checks them:** a model source (`MUNEER_HF_REPO` or `MUNEER_MODEL_PATH`), numbers in range, and, when serving
   without `--no-auth`, `MUNEER_API_KEY`. A bad one stops it with exit code 2 and names the setting.
3. **Gets the weights** (`resolve_model`): uses `MUNEER_MODEL_PATH`, or downloads into `MUNEER_DOWNLOAD_DIR` only the
   files inference needs (`MODEL_FILE_PATTERNS`: config, tokenizer and `.safetensors`). Later starts reuse them.
4. **Builds the `vllm serve` command** (`build_vllm_command`) with Muneer's settings.
5. **Replaces itself with vLLM** (`exec`), with an environment (`build_vllm_env`) that has no Hugging Face token, no
   usage statistics, the two penalties, and `server/` on `PYTHONPATH` so vLLM can load the key check and the
   penalties middleware.

| Option | Effect |
|---|---|
| `--dry-run` | Prints the vLLM command and exits |
| `--download-only` | Gets the weights and exits |
| `--no-auth` | Serves without a key, for local testing only |
| `--env-file PATH` | Reads another settings file |

## The API key

vLLM's own `--api-key` guards only some routes (`/v1`, `/v2`, `/inference` and `/cohere`), which leaves `/invocations`,
`/tokenize` and `/metrics` open. So the launcher loads `muneer_auth.ApiKeyMiddleware` instead, which needs
`Authorization: Bearer <key>` on **every** route except `/health` and `/ping`.

- **Compared safely:** SHA-256 digests, compared in constant time.
- **Refused requests** get HTTP 401, `{"error": "Unauthorized"}`. `OPTIONS` requests need the key too.
- **The key stays off the command line:** the check reads `MUNEER_API_KEY` from the environment.

## Chat format and sampling

One visitor message reaches the model as:

```text
<|im_start|>system
Answer in English unless the user used another language.<|im_end|>
<|im_start|>user
What is Islam?<|im_end|>
<|im_start|>assistant
```

- **The system prompt comes first, for every caller** ([ADR 0002](../docs/adr/0002-no-system-prompt-for-muneer.md)).
  The launcher reads [`system_prompt.md`](system_prompt.md) and passes it to vLLM as
  `--default-chat-template-kwargs '{"default_system_prompt": ...}'`. The template adds it to every conversation that
  brings no system message, which is what the pipeline sends, so the app and callers of the API get the same prompt;
  a request's own system message replaces it. `MUNEER_SYSTEM_PROMPT_FILE` points at another file, and an empty file
  adds none. Restart the server after an edit.
- **No `<think>` blocks.**
- **Defaults:** temperature 0.3, top-p 0.95, top-k 20, repetition penalty 1.1, frequency penalty 0.2, presence
  penalty 0. A request can override them.
- **The two penalties:** vLLM has no server-wide setting for the frequency and presence penalties, so
  `muneer_sampling.PenaltyDefaultsMiddleware` adds them to every request to an OpenAI-style generation route
  (`/v1/chat/completions`, `/v1/completions`, `/v1/responses`, their batch form and `/invocations`) that leaves them
  out. It runs after the key check. vLLM's Anthropic- and Cohere-style routes keep its default of 0.
- **Reply cap:** 1,500 tokens (`MUNEER_MAX_OUTPUT_TOKENS`). A request can't raise it.
- **Context window:** 8,192 tokens (`MUNEER_MAX_MODEL_LEN`).

## Settings

[`.env.example`](.env.example) lists them all. The required ones:

| Setting | What it is |
|---|---|
| `MUNEER_HF_REPO` | The private Hugging Face repo with the weights, such as `<hf-user>/muneer-14b` |
| `HF_TOKEN` | A read-only token for that repo. vLLM never receives it |
| `MUNEER_API_KEY` | The key callers send |

`MUNEER_MODEL_PATH` replaces the first two with weights already on disk. `MUNEER_QUANTIZATION=fp8` fits the model on a
24 GB GPU instead of 48 GB.

## Run it

On a Linux machine with an NVIDIA GPU (driver 580 or newer), from the repository root. The
[deployment guide](../deploy/README.md#1-put-the-weights-in-a-private-hugging-face-repo) shows how to put the weights on
Hugging Face.

```bash
cp server/.env.example server/.env
```

```bash
pip install -r server/requirements.txt
```

```bash
python server/launch.py
```

The first start downloads about 30 GB. The server is ready when `/health` answers. Check it from any machine:

```bash
python server/smoke_test.py --base-url http://127.0.0.1:8000 --api-key <muneer-api-key>
```

The smoke test checks `/health`, that routes refuse a missing key, that `muneer` is listed, and that a chat reply
comes back, whole and streamed.

Any OpenAI client can call it:

```python
from openai import OpenAI

client = OpenAI(base_url="http://127.0.0.1:8000/v1", api_key="<muneer-api-key>")
reply = client.chat.completions.create(model="muneer", messages=[{"role": "user", "content": "What is Islam?"}])
print(reply.choices[0].message.content)
```
