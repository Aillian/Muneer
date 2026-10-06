# The guardrail reviews the filled reply; Muneer's history keeps the raw reply

Placeholder filling runs before the guardrail, so the guardrail judges exactly the text the visitor would see.

Muneer's own conversation history keeps the raw reply. The model therefore sees its previous turns in the form it was trained on, including placeholders once a model is trained with them. The review queue stores both versions.

## Considered options

- **The guardrail reviews the raw reply.** Its corrections would stay organisation-neutral, ready for training, but it would not see the text the visitor actually gets.

## Consequences

A guardrail correction contains the organisation's details. They must be turned back into placeholders before the correction becomes a training example.

## Update

The guardrail now only classifies; it no longer writes corrections. When a da'i writes the right answer for a training example from the review queue, the organisation's details still go back in as placeholders.
