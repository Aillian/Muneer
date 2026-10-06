"""The Gradio app, in four tabs: the visitor's chat, a live view of what happens
behind the scenes for each message, the review queue, and every stored
conversation (behind the admin passcode).

All the work happens in the pipeline; this module only shows it. Run it with
`python -m app` (see __main__.py).
"""

from __future__ import annotations

import hashlib
import html
import logging
import secrets
import sqlite3

import gradio as gr

from pipeline.conversation import Message
from pipeline.conversation_log import ConversationLog, LoggedConversation
from pipeline.guardrail import Label
from pipeline.pipeline import Conversation, Pipeline, Trace
from pipeline.placeholders import DEFAULT_CLOSE, DEFAULT_OPEN
from pipeline.review_queue import FlaggedReply, ReviewQueue
from pipeline.simulated_dai import DEFAULT_NAME as DEFAULT_DAI_NAME

log = logging.getLogger(__name__)

MUNEER_LABEL = "Muneer · AI"
DAI_LABEL = "Da'i (simulated by AI)"

HEADER = """
<div class="muneer-header">
  <div class="muneer-title">Muneer <span lang="ar" dir="rtl">منير</span></div>
  <div class="muneer-tagline">An AI da'i that answers questions about Islam in your own language,
  and hands you to a da'i when a person should take over.</div>
</div>
"""
DEMO_NOTE = (
    "**Demo:** every conversation is stored, exactly as you type it, so that Muneer's answers can be reviewed "
    "and improved. Please don't share real personal details. Muneer currently speaks English, French, Spanish, "
    "Portuguese, Swahili and Filipino."
)
ASK = "Ask anything about Islam…"
ENDED = "This conversation has ended. Start a new conversation to chat with Muneer again."
FEED_INTRO = (
    "**Live:** what happens behind the scenes for each message of this conversation, newest first: Muneer's "
    "raw reply, placeholder filling, the guardrail's verdict and, after a handoff, the da'i's reply."
)
FEED_EMPTY = "Nothing yet. Send a message in the **Chat** tab, and its steps appear here as soon as they are done."
OUTCOMES = {
    "delivered": "Reply shown",
    "handed_off": "Reply withheld, handed off to the da'i",
    "stopped": "Conversation closed at the visitor's request",
    "dai_replied": "Answered by the da'i",
    "error": "Not answered, the visitor was asked to send it again",
    "rejected": "Message refused",
    "closed": "Conversation already closed",
}
REVIEW_HEADERS = ["#", "Time (UTC)", "Label", "Visitor's message", "Withheld reply", "Guardrail's explanation"]
CONVERSATIONS_INTRO = (
    "**Every conversation, as stored in the database:** each message exactly as typed and, for every visitor "
    "message, Muneer's raw reply, placeholder filling, the guardrail's verdict and the simulated da'i's reply."
)
CONVERSATIONS_LOCKED = "🔒 Enter the admin passcode to see every conversation."
CONVERSATIONS_NO_PASSCODE = "🔒 This tab is locked: no admin passcode is set (`ADMIN_PASSCODE`)."
CONVERSATION_HEADERS = ["Started (UTC)", "Conversation", "Visitor's first message", "Messages", "State", "Handoff"]
STATES = {"muneer": "Open", "dai": "With the da'i", "closed": "Closed"}
ADMIN_VIEW = " (Admin View)"  # marks the tabs the admin passcode opens up

# The challenge's identity: navy #12183F, violet #6150EA, turquoise #2EF2C2, Readex Pro.
VIOLET = gr.themes.Color(
    c50="#f0eefd", c100="#e1ddfb", c200="#c3bbf7", c300="#a499f3", c400="#8677ef", c500="#6150ea",
    c600="#4e40bb", c700="#3b308c", c800="#27205e", c900="#14102f", c950="#0a0818", name="violet",
)
THEME = gr.themes.Soft(
    primary_hue=VIOLET,
    neutral_hue="slate",
    font=[gr.themes.GoogleFont("Readex Pro"), "ui-sans-serif", "system-ui", "sans-serif"],
)
CSS = """
.muneer-header { background: linear-gradient(120deg, #12183F 0%, #2a2470 55%, #6150EA 100%);
                 padding: 18px 24px; border-radius: 14px; }
.muneer-header, .muneer-header * { color: #fff !important; }
.muneer-title { font-size: 28px; font-weight: 700; line-height: 1.2; }
.muneer-title span { color: #2EF2C2 !important; margin-inline-start: 10px; }
.muneer-tagline { opacity: .88; margin-top: 4px; font-size: 15px; }
.demo-note { font-size: 13px; opacity: .8; }
.trace blockquote { border-inline-start: 3px solid #6150EA; margin: 4px 0 10px; padding: 2px 10px; }
/* Notices: small centred lines instead of chat bubbles, without a copy button. */
.muneer-notice { font-size: 13px; font-style: italic; text-align: center; color: var(--body-text-color-subdued); }
.message-row:has(.muneer-notice) { align-self: center !important; }
.message-row:has(.muneer-notice) .message { background: transparent !important; border: none !important;
                                            box-shadow: none !important; padding: 2px 8px !important; }
.message-row:has(.muneer-notice) + .message-buttons { display: none !important; }
"""


# ---- rendering ------------------------------------------------------------------


def _inert(text: str) -> str:
    """Text a visitor or a model wrote, made safe to show as Markdown: HTML shows as
    text and images don't load, so a stored message can't change the admin's page."""
    return html.escape(text, quote=False).replace("![", "!\\[")


def chat_messages(messages: list[Message], dai_name: str = DEFAULT_DAI_NAME) -> list[dict]:
    """The conversation as Gradio chat messages. Muneer's and the da'i's messages are
    labelled, so the visitor always knows who is answering. Notices are shown as
    small centred lines (Gradio's chat doesn't display "system" messages)."""
    rendered = []
    for m in messages:
        if m.speaker == "visitor":
            rendered.append({"role": "user", "content": _inert(m.text)})
        elif m.speaker == "muneer":
            rendered.append({"role": "assistant", "content": f"**{MUNEER_LABEL}**\n\n{_inert(m.text)}"})
        elif m.speaker == "dai":
            rendered.append({"role": "assistant", "content": f"**{dai_name} · {DAI_LABEL}**\n\n{_inert(m.text)}"})
        else:
            rendered.append({"role": "assistant", "content": f'<div class="muneer-notice">{html.escape(m.text)}</div>'})
    return rendered


def _quote(text: str) -> str:
    return "\n".join(f"> {_inert(line)}" if line.strip() else ">" for line in text.splitlines())


def _timing(latency_ms: int, tokens_in: int | None = None, tokens_out: int | None = None) -> str:
    parts = [f"{latency_ms / 1000:.1f} s"]
    if tokens_in is not None and tokens_out is not None:
        parts.append(f"{tokens_in:,} → {tokens_out:,} tokens")
    return " · ".join(parts)


def trace_markdown(trace: Trace, number: int, review_queue: bool = True, show_time: bool = False) -> str:
    """What happened for one visitor message, step by step. review_queue: whether
    the app has one, so a flagged reply that isn't in it was meant to be.
    show_time: add when the message arrived (UTC) to the heading."""
    when = f" · {trace.created_at[11:19]} UTC" if show_time else ""
    blocks = [f"### Message {number}: {OUTCOMES[trace.outcome]}{when}", "**Visitor**\n" + _quote(trace.visitor_message)]

    if trace.muneer:
        m = trace.muneer
        source = f" · `{m.source.replace('`', '')}`" if m.source else ""
        blocks.append(f"**1. Muneer's raw reply** · {_timing(m.latency_ms, m.input_tokens, m.output_tokens)}{source}\n"
                      + _quote(m.text))

    if trace.filled:
        f = trace.filled
        if not f.changed:
            blocks.append("**2. Placeholder filling** · no changes")
        else:
            changes = [(f"- removed `{find}`" if not replace else f"- replaced `{find}` with `{replace}`")
                       + (f" ×{n}" if n > 1 else "") for find, replace, n in f.replacements]
            changes += [f"- filled `{DEFAULT_OPEN}{name}{DEFAULT_CLOSE}`" for name, _ in f.placeholders]
            changes += [f"- ⚠️ `{DEFAULT_OPEN}{name}{DEFAULT_CLOSE}` has no value in the organisation profile, so it was "
                        "removed" for name in f.missing]
            blocks.append("**2. Placeholder filling**\n" + "\n".join(changes))
            blocks.append("Filled reply:\n" + _quote(f.text))

    if trace.verdict:
        v = trace.verdict
        blocks.append(f"**3. Guardrail** · {v.model} · {_timing(v.latency_ms, v.input_tokens, v.output_tokens)}")
        blocks.append(f"Label: **{v.label.display_name}** (`{v.label.value}`)\n" + _quote(v.explanation))
        if v.label is not Label.OK:
            if trace.review_id is not None:
                kept = f" It was saved to the review queue as #{trace.review_id}."
            elif review_queue:
                kept = " ⚠️ It couldn't be saved to the review queue."
            else:
                kept = ""
            blocks.append("The visitor never saw this reply." + kept)

    if trace.dai:
        d = trace.dai
        blocks.append(f"**4. Simulated da'i** · {d.model} · {_timing(d.latency_ms, d.input_tokens, d.output_tokens)}")
        blocks.append("The da'i replied once, so the demo ended and the conversation closed.")

    if trace.error:
        blocks.append(f"⚠️ {_inert(trace.error)}")
    return "\n\n".join(blocks)


def feed_markdown(conversation: Conversation | None, pending: str | None = None, review_queue: bool = True) -> str:
    """The behind-the-scenes tab: every message of the conversation, newest first.
    `pending` is the visitor's message while the pipeline is still working on it."""
    traces = conversation.traces if conversation else []
    entries = [trace_markdown(t, n, review_queue) for n, t in reversed(list(enumerate(traces, 1)))]
    if pending:
        entries.insert(0, f"### Message {len(traces) + 1}: in progress…\n\n**Visitor**\n{_quote(pending)}\n\n"
                          "⏳ Working on it: the steps appear here when they are done.")
    return "\n\n---\n\n".join(entries) if entries else FEED_EMPTY


def review_rows(items: list[FlaggedReply]) -> list[list[str]]:
    return [
        [str(i.id), i.created_at[:16].replace("T", " "), i.label.display_name, i.visitor_message, i.filled_reply,
         i.explanation]
        for i in items
    ]


def review_view(queue: ReviewQueue | None, limit: int = 100,
                conversation_ids: list[str] | None = None) -> tuple[str, list[list[str]]]:
    """The review queue tab: a summary line and the table rows. It never raises,
    so a queue problem shows as a message, not as an error in the app.

    conversation_ids: show only these conversations (a visitor's own); None shows all."""
    if queue is None:
        return "The review queue is switched off.", []
    try:
        items, total = queue.recent(limit, conversation_ids), queue.count(conversation_ids)
    except (sqlite3.Error, OSError) as err:
        log.error("Could not read the review queue: %s", err)
        return f"⚠️ The review queue couldn't be read ({err}). Check `DATABASE_PATH` and that its folder can be written.", []
    scope = "" if conversation_ids is None else " in your conversations"
    if total == 0:
        return (f"**No flagged replies{scope} yet.** Replies the guardrail withholds from visitors will appear here.",
                [])
    summary = (f"**{total} flagged {'reply' if total == 1 else 'replies'}{scope}**"
               + (f", showing the latest {limit}" if total > limit else "")
               + ". Each one was withheld from the visitor; a da'i can review them and write the right answers for training.")
    return summary, review_rows(items)


def _preview(text: str, length: int = 80) -> str:
    text = " ".join(text.split())
    return text if len(text) <= length else text[: length - 1] + "…"


def conversation_rows(items: list[LoggedConversation]) -> list[list[str]]:
    return [
        [c.started_at[:16].replace("T", " "), c.id[:8], _preview(c.first_message), str(c.visitor_messages),
         STATES.get(c.state, c.state), c.handoff_label.display_name if c.handoff_label else ""]
        for c in items
    ]


def conversations_view(conversation_log: ConversationLog | None, unlocked: bool, limit: int = 100,
                       locked: str = CONVERSATIONS_LOCKED) -> tuple[str, list[list[str]], list[tuple[str, str]]]:
    """The Conversations tab: a summary line, the table rows and the choices for
    opening one. Nothing is read unless the admin passcode was entered, and it
    never raises."""
    if conversation_log is None:
        return "The conversation log is switched off.", [], []
    if not unlocked:
        return locked, [], []
    try:
        items, totals = conversation_log.recent(limit), conversation_log.totals()
    except (sqlite3.Error, OSError, ValueError) as err:
        log.error("Could not read the conversation log: %s", err)
        return f"⚠️ The conversations couldn't be read ({err}). Check `DATABASE_PATH` and that its folder can be written.", [], []
    if totals.conversations == 0:
        return "**No conversations yet.** Each one appears here as soon as it starts.", [], []
    summary = (f"**{totals.conversations:,} {'conversation' if totals.conversations == 1 else 'conversations'}**, "
               f"{totals.messages:,} messages and {totals.turns:,} visitor messages handled"
               + (f"; showing the latest {limit}" if totals.conversations > limit else "")
               + ". Pick one below to read it.")
    choices = [(f"{c.started_at[:16].replace('T', ' ')} · {c.id[:8]} · {_preview(c.first_message, 50) or '(no message yet)'}",
                c.id) for c in items]
    return summary, conversation_rows(items), choices


def turns_markdown(traces: list[Trace], review_queue: bool = True) -> str:
    """Every turn of a stored conversation, oldest first."""
    if not traces:
        return "No visitor messages in this conversation yet."
    return "\n\n---\n\n".join(trace_markdown(t, n, review_queue, show_time=True) for n, t in enumerate(traces, 1))


def conversation_detail(conversation_log: ConversationLog | None, conversation_id: str | None, unlocked: bool,
                        dai_name: str = DEFAULT_DAI_NAME, review_queue: bool = True) -> tuple[list[dict], str]:
    """One stored conversation: its transcript and the steps of every turn.
    Nothing is read unless the admin passcode was entered, and it never raises."""
    if conversation_log is None or not unlocked or not conversation_id:
        return [], ""
    try:
        messages, traces = conversation_log.messages(conversation_id), conversation_log.turns(conversation_id)
    except (sqlite3.Error, OSError, ValueError) as err:
        log.error("Could not read conversation %s: %s", conversation_id[:8], err)
        return [], f"⚠️ This conversation couldn't be read ({err})."
    return chat_messages(messages, dai_name), turns_markdown(traces, review_queue)


def page_id(request: gr.Request | None) -> str | None:
    """Groups the conversations of one browser page in the database. A one-way hash
    of Gradio's session key, which mustn't be stored: it gives access to the page."""
    session = getattr(request, "session_hash", None)
    return hashlib.sha256(session.encode()).hexdigest()[:16] if session else None


def passcode_matches(entered: str | None, passcode: str | None) -> bool:
    """Whether `entered` is the admin passcode."""
    if not passcode or not entered:
        return False
    return secrets.compare_digest(entered.strip().encode(), passcode.encode())


# ---- the app ----------------------------------------------------------------------


def build_ui(pipeline: Pipeline, concurrency: int = 8, review_limit: int = 100,
             admin_passcode: str | None = None, conversation_limit: int = 100) -> gr.Blocks:
    """concurrency: how many visitor messages are processed at once.

    The review queue tab shows each visitor only the flagged replies from their
    own conversations on this page; a refresh or a new tab starts afresh. The
    Conversations tab shows nothing until the admin passcode is entered. With
    admin_passcode set, both tabs offer a passcode box; entering it in either
    unlocks the whole review queue and every stored conversation, until the page
    is refreshed.
    """

    dai_name = pipeline.dai.name
    queue_on = pipeline.review_queue is not None
    conversation_log = pipeline.conversation_log
    locked = CONVERSATIONS_LOCKED if admin_passcode else CONVERSATIONS_NO_PASSCODE

    def start(session_ids: list[str] | None, request: gr.Request):
        conversation = pipeline.start(session_id=page_id(request))
        session_ids = [*(session_ids or []), conversation.id]
        return (conversation, session_ids, chat_messages(conversation.messages, dai_name),
                feed_markdown(conversation, review_queue=queue_on),
                gr.update(value="", interactive=True, placeholder=ASK), gr.update(variant="secondary"))

    def accept(text: str, conversation: Conversation):
        # Show the visitor's message at once, in the chat and the live tab, while the pipeline works on it.
        if not text.strip():
            return gr.skip(), gr.skip(), gr.skip(), gr.skip()
        shown = chat_messages(conversation.messages, dai_name) + [{"role": "user", "content": _inert(text.strip())}]
        return (text, gr.update(value="", interactive=False), shown,
                feed_markdown(conversation, text.strip(), review_queue=queue_on))

    def respond(text: str, conversation: Conversation):
        if not text.strip():
            return gr.skip(), gr.skip(), gr.skip(), gr.skip(), gr.skip(), gr.skip()
        pipeline.handle(conversation, text)
        closed = conversation.state == "closed"
        box = gr.update(interactive=not closed, placeholder=ENDED if closed else ASK)
        # Once the conversation has ended, New conversation is the one thing left to do.
        new_chat_button = gr.update(variant="primary" if closed else "secondary")
        return (conversation, chat_messages(conversation.messages, dai_name),
                feed_markdown(conversation, review_queue=queue_on), box, "", new_chat_button)

    # Gradio drops visibility changes for a tab that hasn't been opened yet, so
    # each tab sets its own passcode box (and panel) again whenever it is shown.
    def load_review(session_ids: list[str] | None, unlocked: bool):
        summary, rows = review_view(pipeline.review_queue, review_limit, None if unlocked else (session_ids or []))
        return summary, rows, gr.update(visible=bool(admin_passcode) and not unlocked)

    def conversations(unlocked: bool):
        summary, rows, choices = conversations_view(conversation_log, unlocked, conversation_limit, locked)
        return summary, rows, gr.update(choices=choices, value=None)

    def load_conversations(unlocked: bool):
        if not unlocked:  # nothing is read while the tab is locked
            return gr.skip(), gr.skip(), gr.skip(), gr.skip(), gr.skip()
        return *conversations(True), gr.update(visible=False), gr.update(visible=True)

    def show_conversation(conversation_id: str | None, unlocked: bool):
        return conversation_detail(conversation_log, conversation_id, unlocked, dai_name, queue_on)

    def unlocker(source: str):
        """The passcode check, from the review queue tab or the Conversations tab."""

        def unlock(entered: str, session_ids: list[str] | None):
            if passcode_matches(entered, admin_passcode):
                return (True, gr.update(value=""), gr.update(value=""), gr.update(visible=False),
                        gr.update(visible=False), gr.update(visible=True),
                        *review_view(pipeline.review_queue, review_limit, None), *conversations(True))
            if source == "review":
                summary, rows = review_view(pipeline.review_queue, review_limit, session_ids or [])
                return (gr.skip(), gr.update(value=""), gr.skip(), gr.skip(), gr.skip(), gr.skip(),
                        "⚠️ Wrong passcode. " + summary, rows, gr.skip(), gr.skip(), gr.skip())
            return (gr.skip(), gr.skip(), gr.update(value=""), gr.skip(), gr.skip(), gr.skip(),
                    gr.skip(), gr.skip(), "⚠️ Wrong passcode. " + locked, gr.skip(), gr.skip())

        return unlock

    private = {"api_visibility": "private"}
    with gr.Blocks(title="Muneer · AI da'i", analytics_enabled=False) as ui:
        conversation = gr.State()
        session_ids = gr.State([])  # this page's conversations (reset on refresh)
        unlocked = gr.State(False)  # admin passcode entered: show everything
        pending = gr.State("")
        gr.HTML(HEADER)
        with gr.Tabs():
            with gr.Tab("Chat"):
                chatbot = gr.Chatbot(show_label=False, height=520, group_consecutive_messages=False,
                                     feedback_options=None, layout="bubble")
                box = gr.Textbox(show_label=False, placeholder=ASK, autofocus=True,
                                 max_length=pipeline.settings.max_message_chars, submit_btn=True)
                new_chat = gr.Button("New conversation", variant="secondary", size="sm")
                gr.Markdown(DEMO_NOTE, elem_classes="demo-note")
            with gr.Tab("Behind the scenes"):
                gr.Markdown(FEED_INTRO)
                feed = gr.Markdown(FEED_EMPTY, elem_classes="trace")
            with gr.Tab("Review queue" + ADMIN_VIEW) as review_tab:
                review_summary = gr.Markdown()
                refresh = gr.Button("Refresh", size="sm")
                with gr.Row(visible=bool(admin_passcode)) as review_lock:
                    passcode = gr.Textbox(type="password", show_label=False, scale=4,
                                          placeholder="Admin passcode, to see every conversation's flagged replies")
                    show_all = gr.Button("Show all", size="sm", scale=1)
                table = gr.Dataframe(headers=REVIEW_HEADERS, wrap=True, interactive=False, max_height=640,
                                     column_widths=["5%", "11%", "12%", "22%", "26%", "24%"])
            with gr.Tab("Conversations" + ADMIN_VIEW) as conversations_tab:
                gr.Markdown(CONVERSATIONS_INTRO)
                conversations_summary = gr.Markdown(
                    locked if conversation_log else "The conversation log is switched off.")
                with gr.Row(visible=bool(admin_passcode) and conversation_log is not None) as conversations_lock:
                    conversations_passcode = gr.Textbox(type="password", show_label=False, scale=4,
                                                        placeholder="Admin passcode")
                    open_conversations = gr.Button("Open", size="sm", scale=1)
                with gr.Column(visible=False) as conversations_panel:
                    refresh_conversations = gr.Button("Refresh", size="sm")
                    conversations_table = gr.Dataframe(
                        headers=CONVERSATION_HEADERS, wrap=True, interactive=False, max_height=360,
                        column_widths=["13%", "10%", "41%", "9%", "12%", "15%"])
                    pick = gr.Dropdown(label="Open a conversation", choices=[], value=None, interactive=True)
                    transcript = gr.Chatbot(label="Transcript", height=480, group_consecutive_messages=False,
                                            feedback_options=None, layout="bubble")
                    turns = gr.Markdown(elem_classes="trace")

        started = [conversation, session_ids, chatbot, feed, box, new_chat]
        ui.load(start, [session_ids], started, **private)
        submitted = box.submit(accept, [box, conversation], [pending, box, chatbot, feed], queue=False, **private)
        responding = submitted.then(
            respond, [pending, conversation], [conversation, chatbot, feed, box, pending, new_chat],
            concurrency_limit=concurrency, show_progress_on=[chatbot], **private,
        )
        # A reply still on its way belongs to the old conversation, so it is dropped.
        new_chat.click(start, [session_ids], started, cancels=[responding], **private)
        review_tab.select(load_review, [session_ids, unlocked], [review_summary, table, review_lock], **private)
        refresh.click(load_review, [session_ids, unlocked], [review_summary, table, review_lock], **private)

        # Either passcode box unlocks both tabs.
        unlock_outputs = [unlocked, passcode, conversations_passcode, review_lock, conversations_lock,
                          conversations_panel, review_summary, table, conversations_summary, conversations_table, pick]
        for trigger, box_used, source in ((show_all.click, passcode, "review"), (passcode.submit, passcode, "review"),
                                          (open_conversations.click, conversations_passcode, "conversations"),
                                          (conversations_passcode.submit, conversations_passcode, "conversations")):
            trigger(unlocker(source), [box_used, session_ids], unlock_outputs, **private)
        listed = [conversations_summary, conversations_table, pick, conversations_lock, conversations_panel]
        conversations_tab.select(load_conversations, [unlocked], listed, **private)
        refresh_conversations.click(load_conversations, [unlocked], listed, **private)
        pick.change(show_conversation, [pick, unlocked], [transcript, turns], **private)
    return ui
