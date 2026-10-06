# mock_muneer

A stand-in for Muneer's model server, so the app and the pipeline run without a GPU. **It is not the real model:** it
returns fixed, scripted replies picked by keywords in the visitor's message. Placeholder filling, the guardrail and the
simulated da'i stay real, so the app still needs `OPENAI_API_KEY`.

## Files

| File | What it does |
|---|---|
| [`scenarios.py`](scenarios.py) | `SCENARIOS`, in the order they are checked, and `pick()`, which chooses one for a message |
| [`app.py`](app.py) | `create_app()`: the routes, the key check, and replies, whole or streamed |
| [`__main__.py`](__main__.py) | `python -m mock_muneer`: reads the settings and starts the server |
| [`requirements.txt`](requirements.txt) | Starlette, uvicorn and python-dotenv |
| [`tests/`](tests/README.md) | The API and the scenarios |

## How a reply is picked

- **Only the visitor's last message counts.**
- **It is normalised:** lower-cased, curly apostrophes made straight, runs of spaces collapsed.
- **The first scenario with a matching phrase wins,** matched as whole words. "This is fine" doesn't pick `greeting`,
  although "this" contains "hi".
- **No match** gives the fallback.
- **The reply names its scenario** in `system_fingerprint` (`mock-muneer/<scenario>`), which Behind the scenes shows.

## The scenarios

Each one is written to reach one path through the pipeline. The label is what the real guardrail is expected to give;
the mock never sets one.

| Scenario | Try typing | It exercises |
|---|---|---|
| `stop` | "Stop messaging me" | Stop: the conversation closes |
| `transfer` | "Can I talk to a real person?" | Transfer requested: a handoff |
| `upset` | "This is useless" | Visitor upset: a handoff, although the reply is polite |
| `controversial` | "What do you think about politics?" | Controversial topic: a handoff |
| `creed_error` | "Where is God?" | A planted error in belief: a handoff |
| `pushy` | "I'm not sure yet" | A planted improper reply: a handoff |
| `said_shahada` | "I said it!" | The consent notice, after a request for a WhatsApp number |
| `convert` | "How do I become a Muslim?" | Placeholders, and the shahada |
| `identity` | "Who are you?" | Replacement rules: the old persona line becomes Muneer's |
| `prayer` | "How do I pray?" | Placeholders filled from the organisation profile |
| `contact` | "What's your email?" | The organisation's own email, which is fine |
| `newsletter` | "Where can I subscribe to the newsletter?" | A placeholder with no value, removed and reported |
| `about_islam` | "What is Islam?" | A plain reply, shown |
| `greeting` | "Hello!" | A plain reply, shown |
| `fallback` | Anything else | A plain reply, shown |

A handoff or Stop closes the conversation, so press **New conversation** before the next try.

## The API

| Route | What it returns |
|---|---|
| `GET /health`, `/ping` | HTTP 200, with no key needed |
| `GET /v1/models` | One model, `MUNEER_MODEL` (default `muneer`) |
| `POST /v1/chat/completions` | The scenario's reply in OpenAI's format, or a stream of one word per chunk |

- **The same key check as the real server:** with `MUNEER_API_KEY` set, every other route needs
  `Authorization: Bearer <key>`. Left empty, the mock checks no key.
- **Token counts are word counts.**
- **Errors look like OpenAI's,** so the OpenAI SDK raises its usual exceptions.

## Settings

| Setting | Default | What it does |
|---|---|---|
| `MUNEER_API_KEY` | Empty | The key clients must send; the app sends the same one |
| `MUNEER_MODEL` | `muneer` | The model name it accepts |
| `MOCK_DELAY_MS` | `0` | A pause before each reply (`.env.example` sets 600) |
| `MOCK_HOST`, `MOCK_PORT` | `127.0.0.1`, `8000` | Where it listens. `--host` and `--port` win over them |

## Run it

From the repository root:

```bash
python -m mock_muneer
```

It listens on http://127.0.0.1:8000, where `.env.example`'s `MUNEER_BASE_URL` already points. Docker Compose runs it
next to the app with `docker-compose.mock.yml` ([deploy](../deploy/README.md#a-laptop-with-the-mock)).

## Adding a scenario

1. Add it to `SCENARIOS` in [`scenarios.py`](scenarios.py), where it should be checked: an earlier scenario with an
   overlapping phrase wins.
2. Write its phrases in lower case, with straight apostrophes.
3. Use only placeholders that `PLACEHOLDERS` in `.env.example` fills (`[newsletter_url]` is missing on purpose).
4. Add an example message to `test_picks_the_scenario`, and run the [tests](tests/README.md).
