# Muneer

Muneer is an AI da'i: a language model that talks with non-Muslims about Islam in their own language. When it should not carry a conversation alone, it hands the visitor to a human da'i.

## Language

### People and organisations

**Visitor**:
A person chatting with Muneer, usually a non-Muslim curious about Islam or a new Muslim.
_Avoid_: User, customer

**Da'i**:
A human caller to Islam who works for the organisation and takes over the conversations Muneer hands off.
_Avoid_: Agent, operator, daee (also the model's old persona name)

**Simulated da'i**:
An AI that plays the da'i after a handoff so the handoff can be demonstrated. It sends one reply, after which the conversation closes, and it is always labelled as simulated.
_Avoid_: Fake da'i, human bot

**Organisation**:
The da'wah association that runs Muneer on its channels.
_Avoid_: Client, tenant

### Conversation

**Reply**:
One message Muneer writes to the visitor.
_Avoid_: Response, answer, completion

**Raw reply**:
A reply exactly as Muneer wrote it, before placeholder filling.

**Filled reply**:
A reply after placeholder filling: the text the visitor would see.

**Pipeline**:
The fixed sequence of steps run for every visitor message, from Muneer writing a reply to the visitor receiving a message.
_Avoid_: Middleware, proxy, gateway

**Turn**:
One visitor message and everything the pipeline did with it: Muneer's raw reply, placeholder filling, the guardrail's verdict and, after a handoff, the simulated da'i's reply, ending in one outcome. A refused message, such as one that is too long, is a turn too. The Behind the scenes and Conversations tabs number turns as Message 1, Message 2 and so on.
_Avoid_: Exchange, round, step (a step is one part of a turn)

**Handoff**:
Passing a conversation from Muneer to a da'i.
_Avoid_: Transfer, escalation, routing

**Notice**:
A message the platform itself shows the visitor, such as the welcome, the AI disclosure, or a handoff or stop notice. Neither Muneer nor a da'i writes it.
_Avoid_: System message, alert

**Consent notice**:
The notice that follows any message asking for the visitor's contact details. It says that sharing them is optional and how they will be used.

**Closed conversation**:
A conversation that ended, either because the visitor asked to stop or because the simulated da'i has replied. Nothing more is sent in it; the visitor starts a new conversation to chat with Muneer again.

### Placeholder filling

**Placeholder**:
A named slot in a reply that stands for an organisation detail, such as its name or support email.
_Avoid_: Variable, template tag

**Organisation profile**:
The organisation's details that fill the placeholders, such as its name, emails and links.
_Avoid_: Config, branding

**Replacement rule**:
A fixed piece of text the model learned, such as an old persona name, paired with the text to show instead.
_Avoid_: Alias, patch

**Placeholder filling**:
The pipeline step that turns a raw reply into a filled reply, using the organisation profile and the replacement rules.
_Avoid_: Middleware, post-processing

### Guardrail

**Guardrail**:
A second language model that reviews each reply in the context of the whole conversation.
_Avoid_: Moderator, filter, judge

**Label**:
One of the seven outcomes the guardrail gives a reply: OK, Improper response, Wrong from an Islamic point of view, Controversial topic, Transfer requested, Visitor upset, Stop.
_Avoid_: Category, class

**Verdict**:
The guardrail's judgement on one reply: its label and a short explanation. The guardrail only classifies; it never writes a reply.
_Avoid_: Result, decision

**Flagged reply**:
A reply whose label is anything other than OK.

**Withheld reply**:
A flagged reply that is held back, so the visitor never sees it.

**Review queue**:
Flagged replies and their verdicts, waiting for a da'i to write the right answers and turn them into training examples. It is kept in the database, with emails and phone numbers masked.
_Avoid_: Error log

### Storage

**Database**:
The one SQLite file (`DATABASE_PATH`) that holds the conversation log and the review queue. Deleting it starts an empty one.
_Avoid_: Data store, backend, review queue file

**Conversation log**:
Every conversation, its messages and its turns, stored in the database exactly as typed, with nothing masked. It is kept so that Muneer's answers can be reviewed and improved. The Conversations tab shows it, behind the admin passcode.
_Avoid_: History (what Muneer is sent), chat log, audit log, transcript store
