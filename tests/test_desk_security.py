"""The desk may not be pointed at a foreign host, and its feeds may not read
this machine.

Both are the same failure from opposite sides: whoever can set an environment
variable or a feed URL makes the desk send a request it should not. The bearer
key goes only to the provider the tree was configured for, and a feed URL gets
nothing but the public web over https.
"""

import os
import unittest
from unittest import mock

import desk

_ANSWER = '{"agent":"chief","summary":"ok"}'


def _envelope():
    return {"model": desk.DEFAULT_MODEL,
            "choices": [{"message": {"content": _ANSWER}}]}


class TestAgentUrlAllowlist(unittest.TestCase):
    def test_a_foreign_host_is_refused_before_any_request(self):
        with mock.patch.dict(os.environ, {
                "ZTT_AGENT_URL": "https://evil.example/chat/completions",
                "ZTT_AGENT_LOOPBACK": ""}), \
             mock.patch.object(desk, "_post") as post:
            with self.assertRaises(desk.DeskError):
                desk.chat([], "sk-test")
        post.assert_not_called()

    def test_the_configured_provider_host_is_allowed(self):
        with mock.patch.dict(os.environ, {
                "ZTT_AGENT_URL": "https://api.deepseek.com/chat/completions",
                "ZTT_AGENT_LOOPBACK": ""}), \
             mock.patch.object(desk, "_post", return_value=_envelope()):
            self.assertEqual(desk.chat([], "sk-test")["agent"], "chief")

    def test_loopback_needs_the_explicit_test_flag(self):
        url = "http://127.0.0.1:9/chat/completions"
        with mock.patch.dict(os.environ, {"ZTT_AGENT_URL": url,
                                          "ZTT_AGENT_LOOPBACK": ""}), \
             mock.patch.object(desk, "_post") as post:
            with self.assertRaises(desk.DeskError):
                desk.chat([], "sk-test")
        post.assert_not_called()
        with mock.patch.dict(os.environ, {"ZTT_AGENT_URL": url,
                                          "ZTT_AGENT_LOOPBACK": "1"}), \
             mock.patch.object(desk, "_post", return_value=_envelope()):
            self.assertEqual(desk.chat([], "sk-test")["agent"], "chief")

    def test_the_loopback_flag_does_not_open_other_hosts(self):
        with mock.patch.dict(os.environ, {
                "ZTT_AGENT_URL": "https://evil.example/chat/completions",
                "ZTT_AGENT_LOOPBACK": "1"}), \
             mock.patch.object(desk, "_post") as post:
            with self.assertRaises(desk.DeskError):
                desk.chat([], "sk-test")
        post.assert_not_called()


class TestFeedSchemes(unittest.TestCase):
    def test_local_and_non_https_urls_are_refused(self):
        for url in ("file:///C:/secret.txt", "ftp://example.com/feed",
                    "http://example.com/feed", "https://127.0.0.1/feed",
                    "https://127.0.0.1./feed", "https://localhost/feed",
                    "https://[::1]/feed", "https://[::ffff:127.0.0.1]/feed",
                    "https://169.254.169.254/latest/meta-data/"):
            with self.subTest(url=url):
                self.assertIsNotNone(desk._feed_refusal(url))

    def test_a_public_https_feed_is_allowed(self):
        self.assertIsNone(desk._feed_refusal("https://feeds.example.com/rss"))

    def test_a_refused_feed_is_reported_and_never_fetched(self):
        with mock.patch.object(desk.urllib.request, "build_opener") as opener:
            items = desk.headlines(["file:///C:/secret.txt",
                                    "https://127.0.0.1/feed"])
        opener.assert_not_called()
        self.assertEqual(len(items), 1)
        self.assertIn("FEED ERROR", items[0]["headline"])
        self.assertIn("file:///C:/secret.txt", items[0]["headline"])
        self.assertIn("127.0.0.1", items[0]["headline"])


if __name__ == "__main__":
    unittest.main()
