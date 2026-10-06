# pipeline/prompts

The instructions the pipeline sends to OpenAI. Muneer itself gets no prompt from the pipeline, only the conversation:
its system prompt is the model server's ([`server/system_prompt.md`](../../server/system_prompt.md),
[ADR 0002](../../docs/adr/0002-no-system-prompt-for-muneer.md)).

| | [`guardrail.md`](guardrail.md) | [`simulated_dai.md`](simulated_dai.md) |
|---|---|---|
| **Read by** | [`guardrail.py`](../guardrail.py) | [`simulated_dai.py`](../simulated_dai.py) |
| **Its job** | Give Muneer's latest filled reply one of the seven labels, with a short explanation | Write the simulated da'i's one message after a handoff |
| **Receives** | The conversation and the reply to label, as JSON | The conversation, the handoff notes, its name and the organisation's name, as JSON |
| **Returns** | `explanation`, then `label` | `message` |
| **Holds** | The labels, what counts as normal, the order when several apply, and worked examples (at least three per label) | Its role, what to give for each handoff reason, and its rules on sources, links and honesty |

## How a prompt is sent

Each OpenAI call holds two messages:

1. **System:** the prompt file, word for word. It is the same in every call, so OpenAI caches it.
2. **User:** a fixed line, then the data as JSON on one line.

Both prompts say that everything inside the JSON is content, never instructions, so text a visitor types can't change
a label or steer the da'i. A strict JSON schema, built from `_Answer` in each file, forces the shape of the answer. The
files are read when the app starts, so restart it after an edit.

## The guardrail's data

The data sent for "Where is God?", shown here over several lines:

```json
{
  "conversation": [
    {"from": "visitor", "text": "How do I pray?"},
    {"from": "muneer", "text": "Muslims pray five times a day... write to Bathel at info@bathel.sa."},
    {"from": "visitor", "text": "Where is God?"}
  ],
  "muneer_latest_reply": "God is present everywhere in His essence, so He is with you in every place at every moment."
}
```

And a verdict it could return:

```json
{"explanation": "The reply says God is present everywhere in His essence; Allah is above the Throne.", "label": "wrong_from_islamic_point_of_view"}
```

- **`conversation`:** the visitor's and Muneer's messages as the visitor saw them, without notices, up to the last 20.
- **`muneer_latest_reply`:** the filled reply to label.
- **Anything else fails:** a refusal, malformed JSON or an unknown label counts as a failed attempt.

## The simulated da'i's data

After that handoff, the da'i gets the same conversation, plus:

```json
{
  "handoff": {
    "reason": "Wrong from an Islamic point of view",
    "reviewer_note": "The reply says God is present everywhere in His essence; Allah is above the Throne.",
    "withheld_reply": "God is present everywhere in His essence, so He is with you in every place at every moment."
  },
  "your_name": "Mohammad",
  "organisation_name": "Bathel"
}
```

It returns `{"message": "..."}`. The code then rejects an empty message, or one with a link or an email address.
`organisation_name` is `org_name` from the profile. No other profile value is sent as a field of its own, though the
conversation and the withheld reply can contain them.

## Editing the prompts

The tests read the prompts, so keep what they look for:

| Prompt | Keep |
|---|---|
| `guardrail.md` | The headings `## Examples` and `## Your answer`; the seven label codes as in `Label`; each example as two `json` blocks: the data (`conversation`, then `muneer_latest_reply`, ending with a visitor message) and the verdict (`explanation`, then `label`); at least three examples per label; and the phrases `test_prompt_is_tidied_for_the_public_repo` looks for |
| `simulated_dai.md` | The phrases `test_prompt_keeps_the_da_i_honest` looks for: that it is "simulated by AI", sends "exactly one message", asks the visitor nothing, invents nothing about itself, and never includes a link or an email |

- **The data and the answer's fields** are built in code: change `_request` or `_Answer` in `guardrail.py` or
  `simulated_dai.py`, then the prompt and its tests.
- **Keep the prompt fixed:** anything that changes from call to call belongs in the data, so the cache keeps working.

Then run the two test files, which need no OpenAI key:

```bash
python -m pytest pipeline/tests/test_guardrail.py pipeline/tests/test_simulated_dai.py
```
