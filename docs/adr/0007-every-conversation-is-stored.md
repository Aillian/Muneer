# Every conversation is stored, exactly as typed

Muneer's answers can only be reviewed and improved if the conversations they came from are kept. Every conversation, message and turn is therefore stored in one SQLite database (`DATABASE_PATH`, `data/muneer.sqlite3` by default), exactly as the visitor typed it, with nothing masked. A da'i can then read Muneer's mistakes in context and turn them into training examples. The app shows the stored conversations in a fourth tab, Conversations, behind the admin passcode (`ADMIN_PASSCODE`).

## What is stored

- **Each conversation:** when it started, the browser page it came from, its state (open, with the da'i or closed) and the label that handed it off, if any.
- **Every message the visitor saw, in order:** the visitor's, Muneer's filled replies, the simulated da'i's and the notices.
- **One turn per visitor message:** the steps the Behind the scenes tab shows. Muneer's raw reply with its model, time and tokens, placeholder filling, the guardrail's verdict, the simulated da'i's reply, any error and the outcome. A refused message, such as one that is too long, is a turn too.
- **The review queue** shares the file and is unchanged: emails and phone numbers are still masked there.

A conversation is saved with the visitor's first message. Saving never breaks the chat: a failure is logged and the conversation carries on. If the database can't be opened at start, the app runs without it.

## Considered options

- **Mask personal details, as the review queue does.** Turned down in favour of the full text: a review needs the conversation exactly as it happened, and the masking only catches emails and phone numbers anyway. Visitors are asked instead not to share real personal details.
- **Save each conversation as soon as the page opens.** Every visit that sent nothing filled the list with empty conversations.
- **A database server, such as PostgreSQL.** One more service to run and secure, for a single app process. A SQLite file on the volume is enough.

## Consequences

- **Visitors are told:** the note under the chat says every conversation is stored, exactly as typed, so that Muneer's answers can be reviewed and improved, and asks them not to share real personal details.
- **The admin passcode guards it:** `ADMIN_PASSCODE` opens the Conversations tab and the whole review queue, until the page is refreshed; it replaces `REVIEW_QUEUE_PASSCODE`. Without it set, the tab stays locked. Nothing limits guesses, so it must be long and random.
- **Nothing said reaches the app's logs:** they still record only what happened to each message and its label.
- **The file stays out of the repository and the images:** `.gitignore` and `.dockerignore` leave out `data/` and every `*.sqlite3` file. Docker Compose keeps it in the `database` volume; the all-in-one image keeps it on the pod's volume, in a folder of its own.
- **Deleting the file starts an empty database,** even while the app runs. Nothing is deleted automatically: there is no retention period.
- **The earlier guides no longer hold:** they said conversations themselves weren't stored, only flagged replies. `DATABASE_PATH` also replaces `REVIEW_QUEUE_PATH`.
