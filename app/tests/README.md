# app/tests

Tests for the [app](../README.md), all in [`test_ui.py`](test_ui.py). They need no browser, no running server, no GPU
and no OpenAI calls.

## What they cover

- **Chat:** each message carries the right label, and **New conversation** cancels a reply still on its way.
- **Behind the scenes:** every step and outcome of a turn is shown. A flagged reply says it was saved to the review
  queue only when it was, and warns when it couldn't be.
- **Review queue:** a visitor sees only their own flagged replies, with no filter all are shown, and a broken database
  shows a message instead of an error.
- **Conversations:** nothing is read until the passcode is entered; then conversations are listed and opened.
- **Safety:** HTML a visitor types is shown as text, the page id is stored as a hash, and a short passcode stops the
  app.
- **The whole app** builds from the `.env.example` settings.

## How they work

- **Traces are built by hand,** so no model is called.
- **Databases are throwaway files** in a temporary folder.
- **The event wiring is read** from the built app's Gradio config; no browser clicks.

## Run them

From the repository root:

```bash
python -m pytest app/tests
```
