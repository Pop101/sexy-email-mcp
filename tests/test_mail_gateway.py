"""Focused tests for account routing, sender authority, templates, and signatures."""
import contextlib
import email
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

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

    def test_revoking_outgoing_alias_blocks_a_prepared_draft(self):
        draft = server.prepare_email("primary", ["person@example.com"], "Hi", "Body", sender="support@example.net")
        with patch.object(server, "_config", return_value={"primary": {**ROWS["primary"], "send_as": []},
                                                          "secondary": ROWS["secondary"]}):
            with patch.object(server.smtplib, "SMTP_SSL") as smtp, self.assertRaises(Exception):
                server.send_draft("primary", draft["draft_id"], approved=True)
            smtp.assert_not_called()

    def test_proactive_checks_report_all_configured_mailboxes(self):
        conn = Mock()
        conn.uid.side_effect = lambda operation, *args: (
            ("OK", [b"1 2"]) if args[-1] == "UNSEEN" else ("OK", [b"1 2 3"])
        )

        @contextlib.contextmanager
        def imap(*args, **kwargs):
            yield conn

        with patch.object(server, "_imap", side_effect=imap):
            status = server.check_mail(limit=0)
        self.assertEqual(set(status["mailboxes"]), {"primary", "secondary"})
        self.assertEqual(status["mailboxes"]["primary"]["unread_count"], 2)

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

    def test_html_templates_escape_variables_and_include_plain_text(self):
        self.templates_file.write_text(json.dumps({"templates": {"styled": {
            "subject": "Hello {name}",
            "body": {"text": "Hi {name},\n\n{body}\n\n{signature}",
                     "html": "<main><p>Hi {name},</p><p>{body}</p>{signature}</main>"},
        }}}))
        self.signatures_file.write_text(json.dumps({"signatures": {"default": {
            "text": "Regards,\nExample Sender", "html": "<table><tr><td>Example Sender</td></tr></table>",
        }}}))
        self.templates_file.chmod(0o600)
        self.signatures_file.chmod(0o600)

        draft = server.prepare_email("primary", ["person@example.com"], body="One <two>\nThree",
                                     template="styled", template_vars={"name": "Recipient & Co"})
        direct = server.prepare_email("primary", ["person@example.com"], "Subject", "<b>Body</b>")

        self.assertIn("Hi Recipient & Co,", draft["body"])
        self.assertIn("Hi Recipient &amp; Co,", draft["html_body"])
        self.assertIn("One &lt;two&gt;<br>\nThree", draft["html_body"])
        self.assertEqual(draft["html_body"].count("Example Sender"), 1)
        self.assertTrue(server.DRAFTS[draft["draft_id"]]["message"].is_multipart())
        self.assertIn("&lt;b&gt;Body&lt;/b&gt;", direct["html_body"])
        self.assertIn("<table><tr><td>Example Sender</td></tr></table>", direct["html_body"])

    def test_template_rejects_unconsumed_body_and_unknown_names(self):
        self.templates_file.write_text(json.dumps({"templates": {"fixed": {"body": "Fixed {signature}"}}}))
        self.templates_file.chmod(0o600)
        with self.assertRaises(Exception):
            server.prepare_email("primary", ["person@example.com"], "Subject", "Extra", template="fixed")
        with self.assertRaises(Exception):
            server.prepare_email("primary", ["person@example.com"], "Subject", "Body", template="missing")
        with self.assertRaises(Exception):
            server.prepare_email("primary", ["person@example.com"], "Subject", "Body", signature="missing")


if __name__ == "__main__":
    unittest.main()
