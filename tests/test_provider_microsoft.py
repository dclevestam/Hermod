import io
import json
import sys
import threading
import unittest
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import providers.microsoft as ms_module
from accounts.descriptors import AccountDescriptor
from providers.common import SyncHealthState
from providers.microsoft import MicrosoftGraphBackend


class _NativeSource:
    def __init__(self):
        self.invalidated = 0

    def get_account(self):
        return object()

    def get_access_token(self, network_ready_fn=None):
        return "ms-token"

    def invalidate_access_token(self):
        self.invalidated += 1


def make_backend():
    source = _NativeSource()
    backend = object.__new__(MicrosoftGraphBackend)
    backend.identity = "user@outlook.com"
    backend.presentation_name = "user@outlook.com"
    backend.accent_color = ""
    backend.provider = "microsoft"
    backend.source_obj = source
    backend.account = source.get_account()
    backend.account_descriptor = AccountDescriptor(
        source="native",
        provider_kind="microsoft-graph",
        identity="user@outlook.com",
        auth_kind="native-oauth2",
        source_obj=source,
    )
    backend._sync_lock = threading.Lock()
    backend._lock = threading.Lock()
    backend._cached_token = None
    backend._cached_token_expiry = 0.0
    backend._folder_cache = {}
    backend._folder_unread = {}
    backend._sync_notices = []
    backend._sync_health = SyncHealthState(
        provider="microsoft",
        account=backend.identity,
        primary_label="Microsoft Graph",
        fallback_label="Unavailable",
    )
    backend._sync_health.mark_ready("Ready")
    return backend


def _graph_response(payload):
    resp = mock.MagicMock()
    resp.__enter__.return_value = resp
    resp.read.return_value = json.dumps(payload).encode("utf-8")
    return resp


class MicrosoftGraphBackendTests(unittest.TestCase):
    def test_fetch_messages_parses_graph_payload_and_caches(self):
        backend = make_backend()
        payload = {
            "value": [
                {
                    "id": "msg-1",
                    "subject": "Hej",
                    "bodyPreview": "Preview",
                    "from": {
                        "emailAddress": {"name": "Alice", "address": "a@live.se"}
                    },
                    "toRecipients": [
                        {"emailAddress": {"name": "", "address": "user@outlook.com"}}
                    ],
                    "receivedDateTime": "2026-04-20T12:34:56Z",
                    "isRead": False,
                    "hasAttachments": False,
                    "conversationId": "conv-1",
                    "internetMessageId": "<abc@live.se>",
                    "parentFolderId": "inbox-id",
                }
            ]
        }

        with mock.patch.object(
            ms_module, "ensure_network_ready"
        ), mock.patch(
            "urllib.request.urlopen", return_value=_graph_response(payload)
        ):
            messages = backend.fetch_messages("inbox", limit=10)

        self.assertEqual(len(messages), 1)
        msg = messages[0]
        self.assertEqual(msg["uid"], "msg-1")
        self.assertEqual(msg["subject"], "Hej")
        self.assertFalse(msg["is_read"])
        self.assertEqual(msg["thread_id"], "conv-1")
        self.assertIn("a@live.se", msg["from"])
        self.assertIsInstance(msg["date"], datetime)
        cached = backend.get_cached_messages("inbox")
        self.assertEqual(len(cached), 1)

    def test_graph_request_retries_after_401(self):
        backend = make_backend()

        responses = [
            urllib.error.HTTPError(
                "https://graph/x", 401, "Unauthorized", hdrs=None, fp=io.BytesIO(b"")
            ),
            _graph_response({"value": []}),
        ]

        def fake_urlopen(*_args, **_kwargs):
            item = responses.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

        with mock.patch.object(
            ms_module, "ensure_network_ready"
        ), mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            result = backend._graph_request("/me/messages")

        self.assertEqual(result, {"value": []})
        self.assertEqual(backend.source_obj.invalidated, 1)

    def test_mark_as_read_patches_and_updates_cache(self):
        backend = make_backend()
        backend._folder_cache["inbox"] = [
            {"uid": "msg-1", "is_read": False, "folder": "inbox"}
        ]
        with mock.patch.object(
            backend, "_graph_request", return_value=None
        ) as graph, mock.patch.object(backend, "_persist_sync_state"):
            ok = backend.mark_as_read("msg-1", folder="inbox")

        self.assertTrue(ok)
        self.assertTrue(backend._folder_cache["inbox"][0]["is_read"])
        path = graph.call_args.args[0]
        self.assertIn("msg-1", path)
        self.assertEqual(graph.call_args.kwargs["method"], "PATCH")
        self.assertEqual(graph.call_args.kwargs["json_body"], {"isRead": True})

    def test_send_message_builds_graph_payload(self):
        backend = make_backend()
        with mock.patch.object(backend, "_graph_request") as graph:
            backend.send_message(
                "Bob <bob@example.com>",
                "Subject",
                "Hello body",
                html="<p>Hello body</p>",
                cc=["cc@example.com"],
            )

        path = graph.call_args.args[0]
        body = graph.call_args.kwargs["json_body"]
        self.assertEqual(path, "/me/sendMail")
        self.assertEqual(body["saveToSentItems"], True)
        message = body["message"]
        self.assertEqual(message["subject"], "Subject")
        self.assertEqual(message["body"]["contentType"], "HTML")
        self.assertEqual(message["toRecipients"][0]["emailAddress"]["address"], "bob@example.com")
        self.assertEqual(message["ccRecipients"][0]["emailAddress"]["address"], "cc@example.com")

    def test_fetch_body_converts_html_and_attachments(self):
        backend = make_backend()
        payload = {
            "body": {"contentType": "html", "content": "<p>Hi</p>"},
            "attachments": [
                {
                    "id": "att-1",
                    "name": "file.pdf",
                    "size": 1024,
                    "contentType": "application/pdf",
                    "isInline": False,
                }
            ],
        }
        with mock.patch.object(backend, "_graph_request", return_value=payload):
            body = backend.fetch_body("msg-1")

        self.assertEqual(body["html"], "<p>Hi</p>")
        self.assertEqual(body["text"], "")
        self.assertEqual(len(body["attachments"]), 1)
        self.assertEqual(body["attachments"][0]["name"], "file.pdf")


if __name__ == "__main__":
    unittest.main()
