# The guardrail checks every reply before the visitor sees it

Every reply Muneer writes is reviewed by the guardrail first. The visitor sees it only if the guardrail labels it OK, so nothing is streamed word by word. A wrong answer therefore never reaches the visitor. The cost is a wait of a few seconds per message, shown as "typing…", which is acceptable because replies are short.

## Considered options

- **Stream the reply and check it in parallel.** This is faster, but a flagged reply has already been read by the time the handoff happens. The original production plan also aimed to catch errors "before they reach the beneficiary".
