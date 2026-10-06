# docs/adr

Architecture decision records: one short note per decision, with its reason and, where relevant, the options turned
down and the consequences.

| # | Decision |
|---|---|
| [0001](0001-guardrail-checks-replies-before-delivery.md) | The guardrail checks every reply before the visitor sees it, so nothing is streamed |
| [0002](0002-no-system-prompt-for-muneer.md) | Muneer gets no system prompt, only the conversation, as it was trained. Updated: the model server now adds a short, fixed one |
| [0003](0003-model-on-runpod-app-in-local-docker.md) | For development, the model runs on RunPod and the app in Docker on a laptop |
| [0004](0004-guardrail-reviews-the-filled-reply.md) | The guardrail reviews the filled reply; Muneer's history keeps the raw reply |
| [0005](0005-simulated-dai-after-handoff.md) | In the demo, a simulated da'i, an OpenAI model, takes over after a handoff and replies once |
| [0006](0006-public-demo-in-one-runpod-pod.md) | The public demo runs the model and the app in one RunPod pod |
| [0007](0007-every-conversation-is-stored.md) | Every conversation is stored, exactly as typed, behind the admin passcode |

## Writing a record

Name it `NNNN-short-title.md`, with the next number. Give the decision as the title, then the context and the reason,
and, where they help, the **Considered options** and the **Consequences**. When a later decision changes a record, add an **Update** section
rather than rewriting it.
