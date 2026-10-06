# pipeline/tests

Tests for every step of the [pipeline](../README.md). They need no GPU and make no OpenAI calls.

| File | What it covers |
|---|---|
| [`test_pipeline.py`](test_pipeline.py) | Whole turns: delivery, handoff, Stop, failures, limits, and every turn saved to the database |
| [`test_muneer.py`](test_muneer.py) | The request to Muneer: only the conversation, the trimmed history, the retry |
| [`test_placeholders.py`](test_placeholders.py) | Placeholders and replacement rules, including the rules shipped in `.env.example` |
| [`test_guardrail.py`](test_guardrail.py) | The request, the verdict, the retry, refused settings, and the prompt's examples |
| [`test_simulated_dai.py`](test_simulated_dai.py) | The da'i's request, the link and email check, and its prompt |
| [`test_consent.py`](test_consent.py) | Spotting requests for contact details in six languages |
| [`test_conversation_log.py`](test_conversation_log.py) | Saving and reading conversations, messages and turns, exactly as typed |
| [`test_review_queue.py`](test_review_queue.py) | Saving and listing flagged replies, and masking |

## The stand-ins

- **[`fakes.py`](fakes.py): `FakeOpenAI`** is an OpenAI-compatible server inside the test process. Each call plays
  back the next scripted outcome: an answer, a refusal, an HTTP error or a network failure. `fake.requests` keeps what
  was sent.
- **The [mock](../../mock_muneer/README.md)** stands in for Muneer's model server, through Starlette's test client.
- **`StubGuardrail` and `StubDai`** in `test_pipeline.py` return scripted labels and messages.
- **Databases** go in pytest's `tmp_path`, never in `data/`.

## Run them

From the repository root:

```bash
python -m pytest pipeline/tests
```
