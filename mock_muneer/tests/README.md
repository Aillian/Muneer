# mock_muneer/tests

Tests for the [mock](../README.md), all in [`test_mock_muneer.py`](test_mock_muneer.py). They need no server, no
network and no OpenAI calls.

## What they cover

- **The API:** the health checks are open, every other route needs the key when one is set, and bad requests get the
  same errors as from the real server.
- **Replies:** a streamed reply joins up to exactly the whole one.
- **The scenarios:** example messages pick the expected scenario, and every scenario can be reached by its first
  phrase.
- **Placeholders:** every placeholder in a reply has a value in `.env.example`, except the one left out on purpose.
- **Where it listens:** the settings file, and `--host` and `--port` winning over it.

## How they work

The mock runs inside the test process, through Starlette's test client. Some tests call it with the real OpenAI SDK,
as the pipeline does.

## Run them

From the repository root:

```bash
python -m pytest mock_muneer/tests
```
