# A simulated da'i takes over after a handoff

To demonstrate a handoff without a human on call, an AI plays the da'i once the guardrail hands a conversation off. The simulated da'i:

- uses the same OpenAI account as the guardrail;
- reads the guardrail's verdict as private notes;
- answers in the visitor's language.

It is deliberately not checked by the guardrail, because it stands in for a human, and human replies are not checked.

## Transparency

The scientific standard requires disclosure wherever visitors might think they are talking to a human. Every simulated da'i message is therefore labelled "Da'i (simulated by AI)", and the simulated da'i says so if asked.

## Consequences

In a real deployment, the handoff goes to the organisation's human da'is instead, and this component is switched off.

## Update

The simulated da'i now sends exactly one message and the conversation then closes. One reply is enough to show the handoff, and visitors don't chat at length with the general-purpose model behind it.

- **The reply:** the da'i, named Mohammad (`SIMULATED_DAI_NAME`), introduces itself as one of the organisation's da'is (Bathel in the demo), and gives the visitor what Muneer couldn't.
- **The closing notice:** it says that Muneer's turn ends, that the da'i takes it from here, that the da'i is simulated by AI in this demo, and that a new conversation starts again with Muneer.
- **Transparency:** with no second message, the da'i can't be asked whether it is an AI, so disclosure now rests on the label "Da'i (simulated by AI)" and the closing notice.
- **No links or emails:** the reply points nowhere outside the chat. The da'i gets only the organisation's name, its instructions forbid links, websites and email addresses, and the code asks again once for a message that has one, then treats the da'i as unavailable.
