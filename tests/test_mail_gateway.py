"""Offline regression tests for the compact Runbox MCP. No real messages are read/sent."""
import asyncio
import base64
import contextlib
import email
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from mcp.client import Client
import mail_gateway as server

ROWS = {
    "primary": {"name": "primary", "address": "primary@example.net",
              "password": "FAKE_TEST_PASSWORD", "send_as": ["support@example.net"]},
    "secondary": {"name": "secondary", "address": "secondary@example.net",
             "password": "FAKE_TEST_PASSWORD", "send_as": []},
}


class GatewayTests(unittest.TestCase):
    def setUp(self):
        server.DRAFTS.clear()
        self.config = patch.object(server, "_config", return_value=ROWS)
        self.config.start()
        self.addCleanup(self.config.stop)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.templates_file = root / "templates.json"
        self.signatures_file = root / "signatures.json"
        for name, path in (("TEMPLATES", self.templates_file), ("SIGNATURES", self.signatures_file)):
            config_path = patch.object(server, name, path)
            config_path.start()
            self.addCleanup(config_path.stop)

    def test_accounts_and_sender_authority_are_distinct_from_received_aliases(self):
        self.assertFalse(server.list_accounts()["setup_required"])
        draft = server.prepare_email("primary", ["customer@example.com"], "Test", "Body")
        self.assertEqual(draft["from"], "primary@example.net")
        allowed = server.prepare_email("primary", ["customer@example.com"], "Test", "Body",
                                       sender="support@example.net")
        self.assertEqual(allowed["from"], "support@example.net")
        with self.assertRaises(Exception):
            server.prepare_email("primary", ["customer@example.com"], "Test", "Body",
                                 sender="unverified-routing-alias@example.net")

    def test_routed_delivery_preserves_multiple_recipient_headers(self):
        message = email.message_from_string(
            "From: customer@example.org\nTo: sales@example.net\n"
            "Delivered-To: primary@example.net\nDelivered-To: sales@example.net\n"
            "X-Original-To: alias@example.net\nX-Forwarded-To: support@example.net\n"
            "Subject: Routed\nMessage-ID: <origin@example.org>\n\nBody"
        )
        with patch.object(server, "_message", return_value=message):
            result = server.read_message("primary", "123")
            draft = server.prepare_reply("primary", "123", "Answer")
        self.assertEqual(result["to"], ["sales@example.net"])
        self.assertEqual(result["delivered_to"], ["primary@example.net", "sales@example.net"])
        self.assertEqual(result["x_original_to"], ["alias@example.net"])
        self.assertEqual(result["x_forwarded_to"], ["support@example.net"])
        self.assertEqual(draft["from"], "primary@example.net")
        self.assertEqual(draft["to"], ["customer@example.org"])
        self.assertEqual(draft["subject"], "Re: Routed")
        self.assertEqual(server.DRAFTS[draft["draft_id"]]["message"]["In-Reply-To"], "<origin@example.org>")

    def test_send_requires_approval_and_does_not_resend(self):
        draft = server.prepare_email("primary", ["person@example.com"], "Hi", "Text")
        token = draft["draft_id"]
        with self.assertRaises(Exception):
            server.send_draft("primary", token)
        smtp = Mock()
        smtp.send_message.return_value = {}
        conn = Mock()
        conn.list.return_value = ("OK", [b'(\\Sent) "/" "Sent"'])
        conn.append.return_value = ("OK", [])
        @contextlib.contextmanager
        def imap(*args, **kwargs):
            yield conn
        with patch.object(server.smtplib, "SMTP_SSL", return_value=smtp), \
             patch.object(server, "_imap", side_effect=imap):
            first = server.send_draft("primary", token, approved=True)
            second = server.send_draft("primary", token, approved=True)
        self.assertEqual(first["status"], "sent")
        self.assertEqual(second["status"], "already_attempted")
        smtp.send_message.assert_called_once()
        self.assertEqual(smtp.send_message.call_args.kwargs["from_addr"], "primary@example.net")
        conn.append.assert_called_once()

    def test_revoking_outgoing_alias_blocks_a_prepared_draft(self):
        draft = server.prepare_email("primary", ["person@example.com"], "Hi", "Body", sender="support@example.net")
        with patch.object(server, "_config", return_value={"primary": {**ROWS["primary"], "send_as": []}, "secondary": ROWS["secondary"]}):
            with patch.object(server.smtplib, "SMTP_SSL") as smtp, self.assertRaises(Exception):
                server.send_draft("primary", draft["draft_id"], approved=True)
            smtp.assert_not_called()

    def test_message_reads_use_peek_and_readonly(self):
        raw = b"From: person@example.com\nTo: routed@example.net\nSubject: Testing\n\nBody"
        conn = Mock()
        conn.uid.side_effect = [
            ("OK", [b"1 (UID 5 RFC822.SIZE " + str(len(raw)).encode() + b")"]),
            ("OK", [(b"1 (BODY[])", raw)]),
        ]
        @contextlib.contextmanager
        def imap(*args, **kwargs):
            self.assertEqual(args[1], "INBOX")
            yield conn
        with patch.object(server, "_imap", side_effect=imap):
            result = server.read_message("secondary", "5")
        self.assertEqual(result["body"], "Body")
        self.assertEqual(conn.uid.call_args_list[0].args, ("fetch", "5", "(RFC822.SIZE)"))
        self.assertEqual(conn.uid.call_args_list[1].args, ("fetch", "5", "(BODY.PEEK[])"))
        self.assertTrue(result["content_is_untrusted"])

    def test_imap_folder_quoting_and_search_validation(self):
        self.assertEqual(server._quote('Sent "Things"'), r'"Sent \"Things\""')
        for query in ("ALL\nSTORE", "FROM test\r\n", "X" * 1501):
            with self.assertRaises(ValueError):
                server._uids(Mock(), query)

    def test_proactive_checks_report_both_mailboxes_and_do_not_send(self):
        conn = Mock()
        conn.uid.side_effect = lambda operation, *args: (
            ("OK", [b"1 2"]) if args[-1] == "UNSEEN" else ("OK", [b"1 2 3"])
        )
        @contextlib.contextmanager
        def imap(*args, **kwargs):
            yield conn
        with patch.object(server, "_imap", side_effect=imap), \
             patch.object(server.smtplib, "SMTP_SSL") as smtp:
            status = server.check_mail(limit=0)
        self.assertEqual(set(status["mailboxes"]), {"primary", "secondary"})
        self.assertEqual(status["mailboxes"]["primary"]["unread_count"], 2)
        smtp.assert_not_called()

    def test_connector_listing_preserves_account_folder_and_newest_first(self):
        conn = Mock()
        @contextlib.contextmanager
        def imap(account, folder):
            self.assertEqual((account, folder), ("secondary", "INBOX"))
            yield conn
        with patch.object(server, "_imap", side_effect=imap), \
             patch.object(server, "_uids", return_value=[b"1", b"2", b"3"]) as uids, \
             patch.object(server, "_heads", side_effect=lambda _conn, ids: [{"uid": uid.decode()} for uid in ids]), \
             patch.object(server.smtplib, "SMTP_SSL") as smtp:
            result = server.list_messages("secondary", limit=2)
        self.assertEqual([row["uid"] for row in result["messages"]], ["3", "2"])
        uids.assert_called_once_with(conn, "ALL")
        smtp.assert_not_called()

    def test_headers_batch_once_and_preserve_uid_order_and_flags(self):
        conn = Mock()
        conn.uid.return_value = ("OK", [
            (b'1 (UID 2 FLAGS (\\Seen) BODY[HEADER] {39}', b'From: a@example.com\r\nSubject: Old\r\n\r\n'), b')',
            (b'2 (UID 3 FLAGS () BODY[HEADER] {39}', b'From: b@example.com\r\nSubject: New\r\n\r\n'), b')',
        ])
        result = server._heads(conn, [b"3", b"2", b"1"])
        self.assertEqual([row["uid"] for row in result], ["3", "2"])
        self.assertEqual([row["unread"] for row in result], [True, False])
        conn.uid.assert_called_once_with("fetch", "3,2,1", "(UID BODY.PEEK[HEADER] FLAGS)")
        conn.reset_mock()
        self.assertEqual(server._heads(conn, []), [])
        conn.uid.assert_not_called()

    def test_connector_listing_rejects_bad_limits_before_login(self):
        with patch.object(server, "_imap") as imap:
            for limit in (0, 101):
                with self.assertRaises(Exception):
                    server.list_messages("primary", limit=limit)
        imap.assert_not_called()

    def test_templates_and_signatures_render_with_exactly_one_signature(self):
        self.templates_file.write_text(json.dumps({"templates": {
            "greeting": {"description": "Neutral test template", "subject": "Hello {name}",
                         "body": "Hi {name},\n\n{body}\n\n{signature}"},
            "notice": {"body": "Notice:\n\n{body}"},
        }}))
        self.signatures_file.write_text(json.dumps({"signatures": {"default": "Regards,\nExample Sender"}}))
        self.templates_file.chmod(0o600)
        self.signatures_file.chmod(0o600)

        slotted = server.prepare_email("primary", ["person@example.com"], body="Details",
                                       template="greeting", template_vars={"name": "Recipient"})
        appended_to_template = server.prepare_email("primary", ["person@example.com"], "Subject", "Details",
                                                    template="notice")
        appended_to_body = server.prepare_email("primary", ["person@example.com"], "Subject", "Body")
        unsigned = server.prepare_email("primary", ["person@example.com"], "Subject", "Body", signature="none")

        self.assertEqual(slotted["subject"], "Hello Recipient")
        self.assertEqual(slotted["body"], "Hi Recipient,\n\nDetails\n\nRegards,\nExample Sender")
        self.assertEqual(slotted["body"].count("Example Sender"), 1)
        self.assertEqual(appended_to_template["body"], "Notice:\n\nDetails\n\nRegards,\nExample Sender")
        self.assertEqual(appended_to_body["body"], "Body\n\nRegards,\nExample Sender")
        self.assertEqual(unsigned["body"], "Body")
        self.assertEqual(server.list_signatures(), {"signatures": ["default"], "default_configured": True})
        templates = {row["name"]: row for row in server.list_templates()["templates"]}
        self.assertEqual(templates["greeting"]["variables"], ["name"])

    def test_template_rejects_unconsumed_body_and_unknown_names(self):
        self.templates_file.write_text(json.dumps({"templates": {"fixed": {"body": "Fixed {signature}"}}}))
        self.templates_file.chmod(0o600)
        with self.assertRaises(Exception):
            server.prepare_email("primary", ["person@example.com"], "Subject", "Extra", template="fixed")
        with self.assertRaises(Exception):
            server.prepare_email("primary", ["person@example.com"], "Subject", "Body", template="missing")
        with self.assertRaises(Exception):
            server.prepare_email("primary", ["person@example.com"], "Subject", "Body", signature="missing")

    def test_attachment_indexes_cover_only_leaf_attachments(self):
        message = email.message.EmailMessage()
        message["From"] = "sender@example.com"
        message["To"] = "recipient@example.com"
        message["Subject"] = "Files"
        message.set_content("Body")
        message.add_attachment(b"first", maintype="text", subtype="plain", filename="one.txt")
        message.add_attachment(b"second", maintype="application", subtype="octet-stream", filename="two.bin")
        with patch.object(server, "_message", return_value=message):
            metadata = server.read_message("primary", "123")
            attachment = server.get_attachment("primary", "123", 1)
            with self.assertRaises(Exception):
                server.get_attachment("primary", "123", 2)
        self.assertEqual([row["index"] for row in metadata["attachments"]], [0, 1])
        self.assertEqual([row["filename"] for row in metadata["attachments"]], ["one.txt", "two.bin"])
        self.assertEqual(base64.b64decode(attachment["content_base64"]), b"second")
        self.assertEqual(attachment["size"], 6)

    def test_outgoing_attachments_are_validated_and_mime_encoded(self):
        attachments = [{"filename": "hello.txt", "content_type": "text/plain",
                        "content_base64": base64.b64encode(b"hello").decode()}]
        draft = server.prepare_email("primary", ["person@example.com"], "Hi", "Body", attachments=attachments)
        message = server.DRAFTS[draft["draft_id"]]["message"]
        parts = server._attachment_parts(message)
        self.assertEqual(draft["attachments"], [{"filename": "hello.txt", "content_type": "text/plain", "size": 5}])
        self.assertEqual(len(parts), 1)
        self.assertEqual(parts[0].get_payload(decode=True), b"hello")
        for invalid in ({"filename": "../private", "content_base64": ""},
                        {"filename": "bad.txt", "content_base64": "not base64!"}):
            with self.assertRaises(Exception):
                server.prepare_email("primary", ["person@example.com"], "Hi", "Body", attachments=[invalid])


class DiscoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_slow_search_does_not_block_other_mcp_requests(self):
        started, release = threading.Event(), threading.Event()
        @contextlib.contextmanager
        def imap(*args, **kwargs):
            started.set()
            if not release.wait(3):
                raise RuntimeError("Test release timed out")
            yield Mock()
        with patch.object(server, "_imap", side_effect=imap), \
             patch.object(server, "_uids", return_value=[]), \
             patch.object(server, "_config", return_value=ROWS):
            pending = asyncio.create_task(server.mcp.call_tool("search_messages", {"account": "secondary"}))
            try:
                for _ in range(100):
                    if started.is_set():
                        break
                    await asyncio.sleep(.01)
                self.assertTrue(started.is_set())
                await asyncio.wait_for(server.mcp.call_tool("list_accounts", {}), timeout=.5)
                self.assertFalse(pending.done())
            finally:
                release.set()
                await pending

    async def test_mailbox_polling_publishes_only_new_changes_after_baseline(self):
        events = []
        unsubscribe = server.SUBSCRIPTIONS.subscribe(events.append)
        self.addCleanup(unsubscribe)
        latest = {}
        with patch.object(server, "_config", return_value=ROWS), \
             patch.object(server, "_latest_uid", side_effect=["100", "200", "100", "201", "100", "201"]):
            await server._poll_mailboxes(latest)
            self.assertEqual(events, [])
            await server._poll_mailboxes(latest)
            await server._poll_mailboxes(latest)
        self.assertEqual([event.uri for event in events], ["mail://inbox/secondary"])
        self.assertTrue(server.SUBSCRIPTIONS.has_listeners)
        unsubscribe()
        self.assertFalse(server.SUBSCRIPTIONS.has_listeners)

    async def test_subscription_stream_acknowledges_and_delivers_resource_update(self):
        uri = "mail://inbox/primary"
        with patch.object(server, "_config", return_value=ROWS), \
             patch.object(server, "_latest_uid", return_value="100"):
            async with Client(server.mcp, mode="2026-07-28") as client:
                async with client.listen(resource_subscriptions=[uri]) as subscription:
                    self.assertEqual(subscription.honored.resource_subscriptions, [uri])
                    await server.SUBSCRIPTIONS.publish(server.ResourceUpdated(uri))
                    event = await asyncio.wait_for(anext(subscription), timeout=1)
                    self.assertEqual(event.uri, uri)

    async def test_public_surface_and_proactive_artifacts(self):
        tools = {tool.name: tool for tool in await server.mcp.list_tools()}
        self.assertEqual(set(tools), {"list_accounts", "list_templates", "list_signatures", "check_mail", "list_folders",
                                      "search_messages", "list_messages", "read_message", "get_attachment",
                                      "prepare_email", "prepare_reply", "send_draft"})
        self.assertFalse(tools["send_draft"].annotations.read_only_hint)
        self.assertTrue(tools["list_messages"].annotations.read_only_hint)
        self.assertEqual(tools["send_draft"].input_schema["properties"]["approved"]["type"], "boolean")
        self.assertEqual([prompt.name for prompt in await server.mcp.list_prompts()], ["inbox_brief"])
        self.assertEqual([str(resource.uri) for resource in await server.mcp.list_resources()], ["mail://status"])


if __name__ == "__main__":
    unittest.main()
