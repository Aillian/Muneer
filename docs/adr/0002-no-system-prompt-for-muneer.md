# Muneer gets no system prompt

None of Muneer's 1.4 million training conversations contains a system message, so the pipeline sends it only the conversation itself. Instructing it through a system prompt would push it outside what it was trained on.

## Consequences

Everything the platform must say is added by the pipeline or the UI, never requested from the model:

- the welcome message;
- the AI disclosure;
- handoff and stop notices;
- consent lines.

In production, the chat platform inserted the welcome and the disclosure in the same way.

## Update (5 Oct 2026)

Muneer now gets a short, fixed system prompt before every conversation, at the project owner's request.

The model server owns it: the launcher reads [`server/system_prompt.md`](../../server/system_prompt.md), and the chat
template adds it to every conversation that brings no system message, so the app and callers of the API get the same
prompt. The pipeline still sends only the conversation, as decided above; a request's own system message would replace
the default. The platform's own messages are still added by the pipeline and the UI, as above. An empty file sends no
system prompt.
