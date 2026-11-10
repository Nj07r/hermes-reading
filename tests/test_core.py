"""Unit tests for Reading. No network: Gmail calls go to a fake.

Run: python -m unittest discover -s tests   (with the Hermes venv python)
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="reading-test-")
os.environ["HERMES_HOME"] = TMP

import reading_auth as auth  # noqa: E402
import reading_core as core  # noqa: E402


def meta(frm, subject="Hi", unsub="", list_id="", ts=1000):
    h = [{"name": "From", "value": frm}, {"name": "Subject", "value": subject}]
    if unsub:
        h.append({"name": "List-Unsubscribe", "value": unsub})
    if list_id:
        h.append({"name": "List-Id", "value": list_id})
    return {"h": core._headers({"headers": h}), "ts": ts}


class FakeGmail:
    """Records calls; answers the few endpoints sync() and the senders code use."""

    def __init__(self):
        self.calls = []
        self.next_filter = 0

    def __call__(self, method, path, params=None, body=None, _retry=True):
        self.calls.append((method, path, params, body))
        if path == "/labels" and method == "GET":
            return {"labels": [{"id": "Label_9", "name": "Reading"}]}
        if path == "/settings/filters" and method == "POST":
            self.next_filter += 1
            return {"id": f"f{self.next_filter}"}
        if path == "/messages" and method == "GET":
            q = (params or {}).get("q", "")
            return {"messages": [{"id": "m1"}, {"id": "m2"}]} if "in:inbox" in q else {"messages": []}
        return {}


class PureTests(unittest.TestCase):
    def test_split_from(self):
        self.assertEqual(core.split_from('"Ann Lee" <Ann@Example.com>'), ("Ann Lee", "ann@example.com"))
        self.assertEqual(core.split_from("news@x.io"), ("news@x.io", "news@x.io"))

    def test_parse_unsubscribe_one_click(self):
        u = core.parse_unsubscribe("<mailto:u@x.io?subject=stop>, <https://x.io/u?id=1>", "List-Unsubscribe=One-Click")
        self.assertEqual(u["https"], "https://x.io/u?id=1")
        self.assertEqual(u["mailto"], "mailto:u@x.io?subject=stop")
        self.assertTrue(u["one_click"])
        self.assertTrue(u["available"])

    def test_parse_unsubscribe_no_post_header(self):
        u = core.parse_unsubscribe("<https://x.io/u>")
        self.assertFalse(u["one_click"])
        self.assertTrue(u["available"])
        self.assertFalse(core.parse_unsubscribe("")["available"])

    def test_parse_unsubscribe_http_is_not_https(self):
        u = core.parse_unsubscribe("<http://x.io/u>", "List-Unsubscribe=One-Click")
        self.assertIsNone(u["https"])
        self.assertFalse(u["available"])

    def test_web_url_and_tracker(self):
        body = '<a href="https://t.co/CL0/https:%2F%2Fexample.com%2Fpost/1/abc">View in browser</a>'
        self.assertEqual(core.web_url(body), "https://example.com/post")
        sub = 'x https://open.substack.com/pub/abc/p/hello-world?utm=1 y'
        self.assertEqual(core.web_url(sub), "https://open.substack.com/pub/abc/p/hello-world")

    def test_html_to_text_keeps_links_and_paragraphs(self):
        text = core.html_to_text('<style>p{}</style><h1>Title</h1><p>Read <a href="https://a.io/x">this</a>.</p>'
                                 '<ul><li>one</li><li>two</li></ul>\u200b')
        self.assertIn("Title", text)
        self.assertIn("[this](https://a.io/x)", text)
        self.assertIn("- one", text)
        self.assertNotIn("p{}", text)
        self.assertNotIn("\u200b", text)

    def test_tidy_strips_preheader_padding(self):
        self.assertEqual(core.tidy("a\u034f\u200b  b\u00ad"), "a b")

    def test_normalize(self):
        self.assertEqual(core.normalize([" A@X.io ", "bad", "Name <b@y.org>", "a@x.io"]), ["a@x.io", "b@y.org"])

    def test_group_candidates(self):
        metas = [
            meta("Sub <a@substack.com>", "Issue 2", unsub="<https://substack.com/u>", ts=200),
            meta("Sub <a@substack.com>", "Issue 1", unsub="<https://substack.com/u>", ts=100),
            meta("Shop <deals@shop.com>", "Sale", unsub="<https://shop.com/u>"),
            meta("Friend <f@mail.com>", "Lunch?"),                      # not list mail
            meta("Known <k@x.io>", "x", list_id="<k.x.io>"),              # already routed
            meta("List <l@group.org>", "a", list_id="<l.group.org>"),
            meta("List <l@group.org>", "b", list_id="<l.group.org>"),
        ]
        out = core.group_candidates(metas, skip={"k@x.io"})
        emails = [c["email"] for c in out]
        self.assertEqual(set(emails[:2]), {"a@substack.com", "l@group.org"})  # two each, then newest first
        self.assertNotIn("f@mail.com", emails)
        self.assertNotIn("k@x.io", emails)
        by = {c["email"]: c for c in out}
        self.assertEqual(by["a@substack.com"]["last_subject"], "Issue 2")
        self.assertTrue(by["a@substack.com"]["suggest"])     # platform
        self.assertTrue(by["l@group.org"]["suggest"])        # list id, twice
        self.assertFalse(by["deals@shop.com"]["suggest"])    # one-off shop mail: user decides

    def test_from_query_and_chunks(self):
        self.assertEqual(core.from_query(["a@x.io", "b@y.io"]), "from:(a@x.io OR b@y.io)")
        self.assertEqual(len(core.chunks(list(range(45)), 20)), 3)


class SyncTests(unittest.TestCase):
    def setUp(self):
        for f in (core.senders_path(), core.state_path()):
            f.unlink(missing_ok=True)
        core._C.label_id = None
        self.fake = FakeGmail()
        self.patch = mock.patch.object(core, "_call", self.fake)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()

    def test_add_senders_creates_filters_and_moves_inbox_mail(self):
        report = core.add_senders([f"s{i}@x.io" for i in range(25)])
        self.assertEqual(report["filters_created"], 2)          # 20 + 5
        self.assertEqual(report["moved"], 4)                     # 2 ids per chunk query
        posts = [c for c in self.fake.calls if c[1] == "/settings/filters"]
        action = posts[0][3]["action"]
        self.assertEqual(action["addLabelIds"], ["Label_9"])
        self.assertEqual(set(action["removeLabelIds"]), {"INBOX", "SPAM", "IMPORTANT"})
        self.assertEqual(json.loads(core.state_path().read_text())["filters"], ["f1", "f2"])

    def test_sync_is_idempotent(self):
        core.add_senders(["a@x.io"])
        self.fake.calls.clear()
        core.sync()
        self.assertFalse([c for c in self.fake.calls if c[1].startswith("/settings/filters")])

    def test_remove_replaces_filters(self):
        core.add_senders(["a@x.io", "b@x.io"])
        report = core.remove_senders(["a@x.io"])
        self.assertEqual(report["removed"], ["a@x.io"])
        self.assertEqual(report["filters_deleted"], 1)
        self.assertEqual(core.load_senders(), ["b@x.io"])

    def test_add_rejects_garbage(self):
        with self.assertRaises(core.GmailError):
            core.add_senders(["not an address"])


class AuthTests(unittest.TestCase):
    def test_parse_client_installed_and_flat(self):
        c = auth.parse_client({"installed": {"client_id": "id", "client_secret": "s"}})
        self.assertEqual((c["client_id"], c["token_uri"]), ("id", auth.TOKEN_URI))
        with self.assertRaises(auth.AuthError):
            auth.parse_client({"installed": {"client_id": "id"}})

    def test_save_client_is_private(self):
        path = auth.save_client({"installed": {"client_id": "id", "client_secret": "s"}})
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(auth.load_client()[0]["client_id"], "id")

    def test_parse_redirect(self):
        got = auth.parse_redirect("http://127.0.0.1:5000/?state=abc&code=4/xyz&scope=a")
        self.assertEqual(got, {"code": "4/xyz", "state": "abc"})
        self.assertEqual(auth.parse_redirect("4/bare")["code"], "4/bare")
        with self.assertRaises(auth.AuthError):
            auth.parse_redirect("http://127.0.0.1/?error=access_denied")

    def test_find_token_needs_both_scopes(self):
        shared = Path(TMP) / "google_token.json"
        shared.write_text(json.dumps({"refresh_token": "r", "scopes": [auth.SCOPES[0]]}))
        self.assertIsNone(auth.find_token())
        shared.write_text(json.dumps({"refresh_token": "r", "scopes": auth.SCOPES}))
        self.assertEqual(auth.find_token()[1], shared)
        shared.unlink()

    def test_auth_url_has_pkce_and_offline(self):
        url = auth.auth_url({"client_id": "id"}, "http://127.0.0.1:1/", "st", "ch")
        for part in ("code_challenge=ch", "access_type=offline", "prompt=consent", "gmail.settings.basic"):
            self.assertIn(part, url)

    def test_finish_rejects_other_state(self):
        auth.start({"client_id": "id", "client_secret": "s"}, listen=False)
        with self.assertRaises(auth.AuthError):
            auth.finish("http://127.0.0.1:1/?state=wrong&code=c")


if __name__ == "__main__":
    unittest.main()
