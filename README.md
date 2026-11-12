# Runbox Mail MCP

A focused Model Context Protocol server for up to two Runbox IMAP/SMTP accounts. It provides inbox review, folder listing, search, message reading, attachments, reusable private templates and signatures, threaded drafts, and explicitly approved sending.

The running transport is Streamable HTTP on `127.0.0.1:3110/mcp`. Put it behind your own authenticated HTTPS gateway; do not expose the backend directly.

## Features

- Configured account labels instead of hardcoded email addresses.
- Read-only IMAP folder selection and `BODY.PEEK` reads that preserve unread state.
- Batched message-header retrieval and worker threads for blocking provider calls.
- Routed-recipient headers and separately authorized outgoing aliases.
- Incoming attachment metadata and base64 downloads up to 512 KB per attachment.
- Outgoing base64 attachments on new drafts and replies, up to 10 files and 2 MB combined.
- Private plain-text or HTML templates with simple `{variable}` placeholders.
- Private plain-text or HTML signatures inserted at `{signature}`, or appended when the slot or template is absent.
- Multipart messages with readable plain-text alternatives and HTML-escaped template variables.
- Standard MCP `subscriptions/listen` streams for mailbox change notifications.
- Short-lived in-memory drafts that require explicit approval before sending.

## Setup

Create a virtual environment and install the package:

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
```

Run the interactive credential setup:

```bash
.venv/bin/python scripts/setup.py
.venv/bin/python scripts/setup.py --verify
```

The setup prompts for one or two account labels, Runbox login addresses, and app passwords. Credentials are stored in `.secrets/accounts.json` with mode 0600. The `.secrets/` directory is ignored by Git. To replace credentials, run `scripts/setup.py --replace`.

Account labels are local identifiers such as `primary` or `secondary`; they are not email addresses. Optional `send_as` entries in the private account records authorize additional outgoing From addresses. Incoming recipient headers never authorize an outgoing alias.

## Private templates and signatures

Templates belong in `.secrets/templates.json` and signatures in `.secrets/signatures.json`. Keep both files mode 0600. These files are ignored and must never be committed.

Example template configuration using entirely generic content:

```json
{
  "templates": {
    "follow_up": {
      "description": "Short follow-up with a custom detail paragraph",
      "subject": "Follow-up: {topic}",
      "body": "Hi {name},\n\n{body}\n\n{signature}"
    }
  }
}
```

Example signature configuration:

```json
{
  "signatures": {
    "default": "Regards,\nExample Sender"
  }
}
```

For rich email, replace a template body or signature string with a `{"text": "...", "html": "..."}` pair. Both representations use the same placeholders. HTML markup comes only from these trusted local files; supplied `body` and `template_vars` values are escaped and newlines become line breaks. Use inline styles and email-safe table layouts rather than style blocks or external assets. Messages with HTML include a plain-text alternative, and an HTML signature also works without a template.

`prepare_email` and `prepare_reply` accept:

- `template`: the private template name. Omit it to use the supplied body directly.
- `template_vars`: string values for custom placeholders such as `{name}` and `{topic}`.
- `signature`: the private signature name. It defaults to `default`; use `none` to disable insertion.
- `body`: content for the template's `{body}` slot. If a template has no `{body}` slot, leave this argument empty so content is not silently discarded.

A template's `{signature}` slot is replaced exactly once. If that slot is missing, the selected signature is appended to the rendered body. Without a template, the signature is appended to the supplied body. If no default signature is configured, behavior remains unchanged.

`list_templates` exposes template names, descriptions, subjects, HTML availability, and required variables. `list_signatures` exposes only signature names. Their private bodies are available to the renderer but are not returned by these discovery tools.

## Attachments

`read_message` numbers only actual leaf attachments, starting at zero, and reports filename, content type, and decoded size. `get_attachment` returns the selected attachment as base64. Source messages for attachment retrieval may be up to 25 MB; each returned attachment remains limited to 512 KB.

Pass outgoing attachments to `prepare_email` or `prepare_reply`:

```json
[
  {
    "filename": "notes.txt",
    "content_type": "text/plain",
    "content_base64": "SGVsbG8h"
  }
]
```

The server validates filenames, MIME types, base64 data, individual sizes, and total size before creating a draft. Arbitrary server-side file paths are not accepted.

## Mailbox subscriptions

Agents can subscribe to `mail://inbox/{account}` using the standard [MCP subscription pattern](https://modelcontextprotocol.io/specification/2026-07-28/basic/patterns/subscriptions). Replace `{account}` with a configured account label returned by `list_accounts`.

Open `subscriptions/listen` with a resource filter:

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "subscriptions/listen",
  "params": {
    "notifications": {
      "resourceSubscriptions": ["mail://inbox/primary"]
    }
  }
}
```

The server first sends `notifications/subscriptions/acknowledged` with the accepted filter and subscription ID. Later mailbox UID changes produce `notifications/resources/updated` for the corresponding inbox URI. The notification is a level trigger: read `mail://inbox/{account}` or call `check_mail` to retrieve the current summary.

The first poll establishes a baseline and does not announce old messages. Polling runs every 60 seconds while at least one subscription listener is active and stops when no listeners remain. Set `MAIL_POLL_SECONDS` to adjust the interval; values below five seconds are clamped to five.

Close the SSE response stream to cancel an HTTP subscription. Streams and polling state are not persisted across reconnects or service restarts. Reopen `subscriptions/listen`, establish a new baseline, and refetch current state after reconnecting. There is no replay queue.

Live acknowledgment checks are available without reading message bodies or sending mail:

```bash
.venv/bin/python deploy/subscription_smoke.py
MAIL_PUBLIC_TOKEN=<oauth-access-token> .venv/bin/python deploy/subscription_smoke.py --public https://mcp.example.com/mail
```

## Main tools

- `list_accounts`, `check_mail`, `list_folders`
- `search_messages`, `list_messages`, `read_message`, `get_attachment`
- `list_templates`, `list_signatures`
- `prepare_email`, `prepare_reply`, `send_draft`

`send_draft` requires `approved=true`. Prepare and send are deliberately separate operations. Drafts expire after 30 minutes, are limited to 20 pending records, and cannot be sent twice using the same draft ID. A delivery error after SMTP DATA can be uncertain; check the Sent folder before preparing another attempt.

Set `MAIL_ENABLE_SEND=false` and restart the service to remove `send_draft` from the tool surface.

## Tests

```bash
.venv/bin/python -m unittest discover -s tests -v
```

The focused suite covers account routing, sender authority, templates, and signatures using synthetic data and fake credentials. Transport and subscription behavior is covered by the live smoke probes instead of duplicating SDK tests. No mailbox is accessed and no email is sent by the unit tests. Deployment and service notes are in [DEPLOYMENT.md](DEPLOYMENT.md).

## License and lineage

This project is derived from [simple-email-mcp](https://github.com/mexican75/simple-email-mcp) and retains its MIT license. See `LICENSE` and the Git history.
