# Deployment

The application runs as a systemd service and exposes Streamable HTTP on `127.0.0.1:3110/mcp`. An authenticated HTTPS gateway should terminate public traffic and forward it to this loopback backend.

| Component | Value |
|---|---|
| Backend | `127.0.0.1:3110/mcp` |
| Service | `mail-mcp.service` |
| Account configuration | `.secrets/accounts.json` (0600) |
| Templates | `.secrets/templates.json` (0600, ignored) |
| Signatures | `.secrets/signatures.json` (0600, ignored) |
| Backend token | `MAIL_MCP_TOKEN` in the service environment |
| Public origin | `MAIL_PUBLIC_ORIGIN` when the gateway forwards the public Host or Origin |
| Subscription polling | `MAIL_POLL_SECONDS` (default 60, minimum 5) |
| Deployment files | `deploy/mail-mcp.service`, `deploy/mail-mcp.conf` |

Never commit `.env`, `.secrets/`, account records, private templates, private signatures, gateway tokens, or local backup files.

## Update

```bash
.venv/bin/python -m unittest discover -s tests -v
sudo systemctl restart mail-mcp
.venv/bin/python deploy/smoke_test.py
.venv/bin/python deploy/subscription_smoke.py
```

The smoke test checks backend health, authentication, tool discovery, account status, prompts, and resources without reading or sending mail. Set `MAIL_PUBLIC_URL` to the full public connector URL to include OAuth and public-route checks. The subscription probe opens a modern `subscriptions/listen` stream and verifies its acknowledgment and resource filter. Set `MAIL_PUBLIC_TOKEN` and pass `--public` with the full connector URL to test through the gateway as well.

To recover a failed application update, restore the previous committed `mail_gateway.py` and restart the service. Routine application updates do not require rebuilding the gateway.

To rotate credentials, use `scripts/setup.py --replace` from an interactive terminal. Verify with `scripts/setup.py --verify`; verification authenticates to IMAP and SMTP but sends no mail.

Template and signature files are read when a draft is prepared, so edits do not require a service restart. Keep them mode 0600. They are local operational data, not public source assets.

This is a focused derivative of `mexican75/simple-email-mcp`. The active application is `mail_gateway.py`; credential setup and smoke tests are operational support and are not imported by the server.
