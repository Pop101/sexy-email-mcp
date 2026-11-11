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
- Private templates with simple `{variable}` placeholders.
- Private signatures inserted at `{signature}`, or appended when the slot or template is absent.
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

`prepare_email` and `prepare_reply` accept:

- `template`: the private template name. Omit it to use the supplied body directly.
- `template_vars`: string values for custom placeholders such as `{name}` and `{topic}`.
- `signature`: the private signature name. It defaults to `default`; use `none` to disable insertion.
- `body`: content for the template's `{body}` slot. If a template has no `{body}` slot, leave this argument empty so content is not silently discarded.

A template's `{signature}` slot is replaced exactly once. If that slot is missing, the selected signature is appended to the rendered body. Without a template, the signature is appended to the supplied body. If no default signature is configured, behavior remains unchanged.

`list_templates` exposes template names, descriptions, subjects, and required variables. `list_signatures` exposes only signature names. Their private bodies are available to the renderer but are not returned by these discovery tools.

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

The test suite uses synthetic messages and fake credentials. It does not access a mailbox or send email. Deployment and service notes are in [DEPLOYMENT.md](DEPLOYMENT.md).

## License and lineage

This project is derived from [simple-email-mcp](https://github.com/mexican75/simple-email-mcp) and retains its MIT license. See `LICENSE` and the Git history.
