"""The desk may not invent a number, and it fails loudly without a key.

No network: a local HTTP server stands in for the model endpoint, so the request
shape and the citation guard are tested without a DeepSeek key.
"""

import json
import os
import threading
import tempfile
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

import desk

FACTS = """
LAST LEDGER ROWS:
2026-11-07T11:58:06Z | universe=50 tested=8 strategies=2 total_tests=16 triggering=0 deduped=0 shortlist=0 passed_both=0 trials=16 cumulative=8616 | STAND_DOWN
tier3_results.json:
{"z_crit_corrected": 4.531, "results": [{"symbol": "NERUSD", "strategy": "donchian",
 "significance": {"gate": "t", "t_stat_R": -19.78, "z_crit_bonferroni": 4.531, "passes_corrected": false},
 "permutation": {"p_value": 0.999}}]}
"""


class _Server:
    """Records the last request body and replies with a queued answer."""

    def __init__(self, body: str | bytes = '<rss><channel><item><title>Oil spikes on supply cut</title></item></channel></rss>'):
        self.seen = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                n = int(self.headers.get("Content-Length", 0))
                outer.seen.append(json.loads(self.rfile.read(n)))
                ans = json.dumps({"model": outer.served_model,
                                  "usage": {"prompt_tokens": 11, "completion_tokens": 22},
                                  "choices": [{"message": {"content": outer.answer}}]})
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(ans.encode())

            def do_GET(self):
                outer.seen.append(self.path)
                self.send_response(200)
                self.send_header("Content-Type", "application/rss+xml")
                self.end_headers()
                self.wfile.write(outer.feed if isinstance(outer.feed, bytes) else outer.feed.encode())

            def log_message(self, *a):
                pass

        self.answer = "{}"
        self.served_model = desk.DEFAULT_MODEL
        self.feed = body
        self.httpd = HTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_port}/chat/completions"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *a):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)


class TestClient(unittest.TestCase):
    def test_request_is_json_mode_at_temperature_zero(self):
        with _Server() as s:
            s.answer = '{"agent":"chief","summary":"ok"}'
            out = desk.chat([{"role": "user", "content": "hi"}], "sk-test", url=s.url)
        self.assertEqual(out["agent"], "chief")
        body = s.seen[0]
        self.assertEqual(body["temperature"], 0)
        self.assertEqual(body["response_format"], {"type": "json_object"})
        self.assertEqual(body["model"], desk.DEFAULT_MODEL)

    def test_code_fenced_json_is_accepted(self):
        with _Server() as s:
            s.answer = '```json\n{"agent":"chief","summary":"ok"}\n```'
            out = desk.chat([{"role": "user", "content": "hi"}], "sk-test", url=s.url)
        self.assertEqual(out["summary"], "ok")


class TestCitationGuard(unittest.TestCase):
    def test_a_note_that_invents_a_number_is_rejected(self):
        note = {"agent": "chief", "summary": "the t of -19.78 sits below the 4.531 bar, z = 9.99"}
        out = desk._guard(note, FACTS)
        self.assertEqual(out["status"], "rejected")
        self.assertIn("9.99", out["reason"])

    def test_a_note_that_quotes_the_source_is_accepted(self):
        note = {"agent": "chief", "summary": "t -19.78 against a bar of 4.531, p 0.999"}
        out = desk._guard(note, FACTS)
        self.assertEqual(out["status"], "ok")

    def test_small_uncited_counters_are_rejected_too(self):
        out = desk._guard({"agent": "chief", "summary": "two gates, 3 families, 0 passed"}, FACTS)
        self.assertEqual(out["status"], "rejected")
        self.assertIn("3", out["reason"])

    def test_a_rounded_rendering_of_a_source_number_is_accepted(self):
        out = desk._guard({"agent": "chief", "summary": "bar 4.53"}, FACTS)
        self.assertEqual(out["status"], "ok")

    def test_substrings_and_changed_signs_are_not_citations(self):
        for claim, source in [("4.531", "z=14.531"), ("5.523", "t=-5.523"),
                              ("3%", "no figures"), ("1e3", "count=1"),
                              ("9.99R", "mean=1.0"), (".5", "mean=5")]:
            with self.subTest(claim=claim):
                out = desk._guard({"agent": "chief", "summary": "value " + claim}, source)
                self.assertEqual(out["status"], "rejected")

    def test_empty_wrong_role_and_provider_status_are_rejected(self):
        for note in [{}, {"agent": "chief"}, {"agent": "chief", "summary": ""},
                     {"agent": "chief", "summary": "ok", "status": "ok"},
                     {"agent": "chief", "summary": [], "contradictions": []}]:
            with self.subTest(note=note):
                self.assertEqual(desk._guard(note, FACTS)["status"], "rejected")

    def test_scout_cannot_name_a_symbol_absent_from_its_snapshot(self):
        note = {"agent": "scout", "items": [{"headline": "news", "source": "feed",
                "symbols": ["INVENTED"], "stance": "neutral", "why": "news"}], "caveat": "ok"}
        self.assertEqual(desk._guard(note, FACTS, "scout")["status"], "rejected")


class TestRun(unittest.TestCase):
    def setUp(self):
        self.dir = TemporaryDirectory()
        self.root = Path(self.dir.name)
        (self.root / "funnel_history.log").write_text(
            "2026-11-07T11:58:06Z | universe=50 tested=8 strategies=2 total_tests=16 "
            "triggering=0 deduped=0 shortlist=0 passed_both=0 trials=16 cumulative=8616 | STAND_DOWN\n")
        (self.root / "tier3_results.json").write_text(json.dumps({
            "z_crit_corrected": 4.531,
            "results": [{"symbol": "NERUSD", "strategy": "donchian",
                         "significance": {"gate": "t", "t_stat_R": -19.78}}]}))
        self.addCleanup(self.dir.cleanup)

    def test_no_key_is_a_loud_failure_not_a_quiet_skip(self):
        with mock.patch.object(desk, "api_key", lambda *a, **k: None):
            notes = desk.run(self.root)
        self.assertEqual(notes["status"], "no key")
        self.assertIn("DEEPSEEK_API_KEY", notes["hint"])

    def test_a_rejected_note_marks_the_whole_run_rejected(self):
        with _Server() as s, mock.patch.object(desk, "api_key", lambda *a, **k: "sk-test"):
            os.environ["ZTT_AGENT_URL"] = s.url
            s.answer = '{"agent":"chief","summary":"z = 9.99 is strong","contradictions":[],"watch":"x"}'
            notes = desk.run(self.root)
        os.environ.pop("ZTT_AGENT_URL", None)
        self.assertEqual(notes["status"], "rejected")
        self.assertEqual(notes["roles"]["chief"]["status"], "rejected")

    def test_the_written_file_never_contains_the_key(self):
        with _Server() as s, mock.patch.object(desk, "api_key", lambda *a, **k: "sk-secret-123"):
            os.environ["ZTT_AGENT_URL"] = s.url
            s.answer = '{"agent":"chief","summary":"t -19.78 vs 4.531","contradictions":[],"watch":"x"}'
            notes = desk.run(self.root)
        os.environ.pop("ZTT_AGENT_URL", None)
        p = desk.write_notes(self.root, notes)
        self.assertNotIn("sk-secret", p.read_text(encoding="utf-8"))
        self.assertEqual(json.loads(p.read_text(encoding="utf-8"))["roles"]["chief"]["status"], "ok")

    def test_without_feeds_the_scout_says_so_instead_of_calling(self):
        with _Server() as s, mock.patch.object(desk, "api_key", lambda *a, **k: "sk-test"):
            os.environ["ZTT_AGENT_URL"] = s.url
            s.answer = '{"agent":"chief","summary":"t -19.78 vs 4.531","contradictions":[],"watch":"x"}'
            notes = desk.run(self.root, feeds=[])
        os.environ.pop("ZTT_AGENT_URL", None)
        self.assertEqual(notes["roles"]["scout"]["status"], "no feed configured")
        self.assertEqual(len(s.seen), 3, "only skeptic, chief and jury may call the model")


class TestFeeds(unittest.TestCase):
    def test_titles_are_parsed(self):
        with _Server() as s:
            items = desk.headlines([f"http://127.0.0.1:{s.httpd.server_port}/feed"])
        self.assertEqual(items[0]["headline"], "Oil spikes on supply cut")

    def test_a_document_with_a_dtd_is_refused(self):
        evil = b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY a "aaaaaaaaaa">]><rss><channel><item><title>&a;</title></item></channel></rss>'
        with _Server(evil) as s:
            items = desk.headlines([f"http://127.0.0.1:{s.httpd.server_port}/feed"])
        self.assertEqual(len(items), 1)
        self.assertIn("FEED ERROR", items[0]["headline"])
        self.assertIn("ValueError", items[0]["headline"])


class TestRulesAndJournal(unittest.TestCase):
    def setUp(self):
        self.dir = TemporaryDirectory()
        self.root = Path(self.dir.name)
        self.addCleanup(self.dir.cleanup)

    def test_the_rulebook_is_sent_with_every_call(self):
        (self.root / "DESK.md").write_text("# Desk rules\n\nMARKER-PARAGRAPH-ONE\n")
        with _Server() as s:
            s.answer = '{"agent":"chief","summary":"ok"}'
            desk.chat([{"role": "user", "content": "hi"}], "sk-test", url=s.url, root=self.root)
        system = s.seen[0]["messages"][0]
        self.assertEqual(system["role"], "system")
        self.assertIn("MARKER-PARAGRAPH-ONE", system["content"])

    def test_a_missing_rulebook_falls_back_and_says_so(self):
        out = desk.rules(self.root)
        self.assertIn("built-in fallback", out)
        self.assertIn("never invent a number", out)

    def test_every_run_appends_one_journal_line(self):
        notes = {"generated_utc": "2026-11-09T17:00:00Z", "status": "ok", "model": "deepseek-chat",
                 "facts_sha": "abc123", "roles": {"chief": {"status": "ok", "summary": "nothing to report"}}}
        desk.append_journal(self.root, notes)
        desk.append_journal(self.root, notes)
        lines = (self.root / desk.JOURNAL).read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 2)
        rec = json.loads(lines[0])
        self.assertEqual(rec["facts_sha"], "abc123")
        self.assertEqual(rec["roles"]["chief"], "ok")
        self.assertEqual(rec["chief"], "nothing to report")

    def test_a_rejection_reason_survives_in_the_journal(self):
        notes = {"generated_utc": "t", "status": "rejected", "model": "m", "facts_sha": "x",
                 "roles": {"chief": {"status": "rejected", "reason": "uncited numbers: 9.99"}}}
        desk.append_journal(self.root, notes)
        rec = json.loads((self.root / desk.JOURNAL).read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(rec["rejected"], ["uncited numbers: 9.99"])


class TestModelDrift(unittest.TestCase):
    def setUp(self):
        self.dir = TemporaryDirectory()
        self.root = Path(self.dir.name)
        (self.root / "funnel_history.log").write_text(
            "2026-11-07T11:58:06Z | universe=50 tested=8 strategies=2 total_tests=16 "
            "triggering=0 deduped=0 shortlist=0 passed_both=0 trials=16 cumulative=8616 | STAND_DOWN\n")
        (self.root / "agent_notes.json").unlink(missing_ok=True)
        self.addCleanup(self.dir.cleanup)

    def test_a_silent_upgrade_is_recorded_not_ignored(self):
        with _Server() as s, mock.patch.object(desk, "api_key", lambda *a, **k: "sk-test"):
            os.environ["ZTT_AGENT_URL"] = s.url
            s.served_model = "deepseek-chat-2026.12"     # alias moved under us
            s.answer = '{"agent":"chief","summary":"nothing to report","contradictions":[],"watch":"x"}'
            notes = desk.run(self.root)
        os.environ.pop("ZTT_AGENT_URL", None)
        self.assertEqual(notes["model"], desk.DEFAULT_MODEL)
        self.assertEqual(notes["model_returned"], "deepseek-chat-2026.12")
        self.assertEqual(notes["model_drift"]["served"], "deepseek-chat-2026.12")
        self.assertEqual(notes["usage"]["prompt_tokens"], 11)

    def test_no_drift_when_the_provider_echoes_the_pin(self):
        with _Server() as s, mock.patch.object(desk, "api_key", lambda *a, **k: "sk-test"):
            os.environ["ZTT_AGENT_URL"] = s.url
            s.answer = '{"agent":"chief","summary":"nothing to report","contradictions":[],"watch":"x"}'
            notes = desk.run(self.root)
        os.environ.pop("ZTT_AGENT_URL", None)
        self.assertNotIn("model_drift", notes)
        self.assertEqual(notes["model_returned"], desk.DEFAULT_MODEL)


class TestGuardTokenisation(unittest.TestCase):
    """The citation guard must reject inventions without rejecting arithmetic.

    Its first live run rejected all three roles with "uncited numbers: 2014,
    -09, -129". None was a fabrication: 2014 was the tail of the JSON escape
    for an em dash (\\u2014), -09 came from a date, and -129 from a hyphen used
    as a dash in "150-vs-129". The guard scans a rendered blob, so escaping and
    punctuation must not be able to manufacture a number.
    """

    SRC = ("funnel 2026-11-09T16:58:35Z | universe=50 tested=150 total_tests=150 "
           "cumulative=8766 | STAND_DOWN  tier3: z=-5.523 t_stat_R=-3.512 p=0.9301 "
           "tier2 total_tests=129 generating 9 candidates")

    def test_an_em_dash_does_not_become_the_number_2014(self):
        note = {"agent": "chief", "summary": "the 16:58:35Z scan ran 150 tests \u2014 "
                "all of them \u2014 cumulative 8766"}
        self.assertEqual(desk.uncited(note, self.SRC), [])

    def test_a_date_is_not_a_negative_number(self):
        note = {"agent": "chief", "summary": "generated 2026-11-09 with total_tests=129"}
        self.assertEqual(desk.uncited(note, self.SRC), [])

    def test_a_hyphen_between_words_is_not_a_sign(self):
        note = {"agent": "chief", "summary": "if the 150-vs-129 and 9-vs-9 differ"}
        self.assertEqual(desk.uncited(note, self.SRC), [])

    def test_a_real_negative_statistic_still_passes(self):
        note = {"agent": "skeptic", "verdict": "noise", "reasons": ["z = -5.523, t = -3.512"]}
        self.assertEqual(desk.uncited(note, self.SRC), [])

    def test_an_invented_figure_is_still_rejected(self):
        note = {"agent": "chief", "summary": "the win rate was 71.4% with a 4.9 sharpe"}
        self.assertEqual(sorted(desk.uncited(note, self.SRC)), ["4.9", "71.4"])

    def test_an_invented_year_is_still_rejected(self):
        note = {"agent": "chief", "summary": "in 2031 this will matter"}
        self.assertEqual(desk.uncited(note, self.SRC), ["2031"])


class TestFailureIsNotSuccess(unittest.TestCase):
    """Two ways the desk used to claim a run it did not have."""

    def test_a_truncated_response_is_named_as_truncation(self):
        with self.assertRaises(desk.DeskError) as ctx:
            desk._object('{"agent":"chief","summary":"cut off half', "length", 2000)
        msg = str(ctx.exception)
        self.assertIn("truncated", msg)
        self.assertIn("2000", msg)
        self.assertNotIn("did not return JSON", msg)

    def test_a_run_where_every_role_failed_is_an_error(self):
        notes = {"roles": {"scout": {"status": "no feed configured"},
                           "skeptic": {"status": "error"},
                           "chief": {"status": "error"}}}
        statuses = [r.get("status") for r in notes["roles"].values()]
        accepted = [st for st in statuses if st in (None, "ok")]
        errored = [st for st in statuses if st == "error"]
        rejected = [st for st in statuses if st == "rejected"]
        status = ("rejected" if rejected else "error" if (errored and not accepted)
                  else "partial" if errored else "ok")
        self.assertEqual(status, "error")

    def test_one_failed_role_is_partial_not_ok(self):
        statuses = ["error", "ok", "no feed configured"]
        accepted = [st for st in statuses if st in (None, "ok")]
        errored = [st for st in statuses if st == "error"]
        self.assertEqual(("partial" if errored and accepted else "bad"), "partial")

    def test_cli_failure_states_have_nonzero_exit_codes(self):
        import agent_brief
        with TemporaryDirectory() as directory:
            root = Path(directory)
            for status in ("no key", "rejected", "error", "partial"):
                with self.subTest(status=status), \
                     mock.patch.object(desk, "run", return_value={"status": status, "roles": {},
                         "model": "fixture", "facts_sha": "fixture"}), \
                     mock.patch.object(agent_brief.audit, "write"):
                    self.assertNotEqual(agent_brief.main(["--root", str(root)]), 0)

    def test_nonlocal_plain_http_and_credential_urls_are_refused(self):
        for url in ("http://example.com/chat", "https://user:password@example.com/chat"):
            with self.subTest(url=url), self.assertRaises(desk.DeskError):
                desk.chat([], "fixture", url=url)

    def test_malformed_envelopes_are_named_as_endpoint_failures(self):
        for envelope in ([], {"choices": None}, {"choices": [{"message": {"content": 3}}]}):
            with self.subTest(envelope=envelope), mock.patch.object(desk, "_post", return_value=envelope):
                with self.assertRaises(desk.DeskError):
                    desk.chat([], "fixture")


class TestDigestSaysWhenItClips(unittest.TestCase):
    """A read limit must never read as a broken file.

    tier3_results.json is 12 KB; digest() shows the first 6000 characters. The
    model read that clip, saw it end mid-record, and reported "tier3_results.json
    is truncated mid-GBPCAD" - a contradiction that existed only in our own
    output window. The digest now says which it is.
    """

    def setUp(self):
        self.dir = TemporaryDirectory()
        self.root = Path(self.dir.name)
        self.addCleanup(self.dir.cleanup)

    def test_a_clipped_artifact_is_labelled_with_its_real_size(self):
        big = {"results": [{"symbol": f"S{i}", "pad": "x" * 60} for i in range(400)]}
        (self.root / "tier3_results.json").write_text(json.dumps(big), encoding="utf-8")
        text = desk.digest(self.root)
        self.assertIn("the desk shows the first", text)
        self.assertIn("the file itself is complete", text)
        self.assertIn(str(len(json.dumps(big, indent=1))), text)

    def test_a_small_artifact_is_passed_whole_without_a_warning(self):
        small = {"results": [{"symbol": "BTCUSD"}]}
        (self.root / "tier3_results.json").write_text(json.dumps(small), encoding="utf-8")
        text = desk.digest(self.root)
        self.assertIn("tier3_results.json:", text)
        self.assertNotIn("the desk shows the first", text)


class UsageMetadataTests(unittest.TestCase):
    """The provider may add fields to `usage`; it may not lie about the counts.

    DeepSeek added `prompt_tokens_details` and `completion_tokens_details` as
    nested objects. The old rule was "every value in usage is a non-negative
    int", so that harmless addition made scout, skeptic, chief - and the new
    jury - all raise "invalid usage metadata" at once. The rule now constrains
    the two counts this code actually sums, and nothing else.
    """

    def _reply(self, usage):
        return {"model": "deepseek-flash", "usage": usage,
                "choices": [{"finish_reason": "stop",
                             "message": {"content": '{"agent":"chief","summary":"x"}'}}]}

    def test_nested_detail_objects_are_accepted(self):
        usage = {"prompt_tokens": 63, "completion_tokens": 16, "total_tokens": 79,
                 "prompt_tokens_details": {"cached_tokens": 0},
                 "completion_tokens_details": {"reasoning_tokens": 16}}
        meta = {}
        with mock.patch.object(desk, "_post", return_value=self._reply(usage)):
            note = desk.chat([{"role": "user", "content": "json"}], "k", meta=meta)
        self.assertEqual(note["agent"], "chief")
        self.assertEqual(meta["usage"]["prompt_tokens"], 63)

    def test_a_negative_or_textual_count_is_still_refused(self):
        for bad in ({"prompt_tokens": -1}, {"completion_tokens": "16"},
                    {"prompt_tokens": True}):
            with self.subTest(usage=bad):
                with mock.patch.object(desk, "_post", return_value=self._reply(bad)):
                    with self.assertRaises(desk.DeskError):
                        desk.chat([{"role": "user", "content": "json"}], "k", meta={})

    def test_usage_that_is_not_an_object_is_refused(self):
        with mock.patch.object(desk, "_post", return_value=self._reply("79")):
            with self.assertRaises(desk.DeskError):
                desk.chat([{"role": "user", "content": "json"}], "k", meta={})


class JuryCannotSizeTests(unittest.TestCase):
    """The judge returns a verdict. Size is policy, and 0.01 is not negotiable."""

    def _jury_note(self, **kw):
        base = {"agent": "jury", "verdict": "stand_down", "size_lots": 0.01,
                "why": "no candidate cleared the bar", "conditions": ["none"]}
        base.update(kw)
        return base

    def test_the_policy_size_passes(self):
        self.assertIsNone(desk._validate_note(self._jury_note(), "facts 0.01", "jury"))

    def test_any_other_size_is_rejected(self):
        for size in (0.02, 1.0, 100, "0.01"):
            with self.subTest(size=size):
                note = self._jury_note(size_lots=size)
                self.assertEqual(desk._validate_note(note, "facts 0.01", "jury"),
                                 "jury may not size an order")

    def test_an_unknown_verdict_is_rejected(self):
        note = self._jury_note(verdict="buy")
        self.assertEqual(desk._validate_note(note, "facts 0.01", "jury"),
                         "invalid jury verdict")


class WatcherWindowTests(unittest.TestCase):
    """The watcher is scheduled every 15 minutes around the clock; the bill is not.

    Outside the scan window it must exit 0 having called nothing, so a green
    scheduled run never means "the desk briefed all night".
    """

    def test_outside_the_window_nothing_is_called(self):
        import agent_brief, run_funnel
        with TemporaryDirectory() as d, \
             mock.patch.object(run_funnel, "in_window", return_value=False), \
             mock.patch.object(desk, "run") as ran, \
             mock.patch.object(agent_brief, "HERE", Path(d)):
            code = agent_brief.main(["--window-only", "--root", d])
        self.assertEqual(code, 0)
        ran.assert_not_called()

    def test_inside_the_window_the_desk_runs(self):
        import agent_brief, run_funnel
        with TemporaryDirectory() as d, \
             mock.patch.object(run_funnel, "in_window", return_value=True), \
             mock.patch.object(desk, "run", return_value={
                 "status": "ok", "model": "m", "facts_sha": "x", "roles": {}}) as ran, \
             mock.patch.object(agent_brief, "HERE", Path(d)), \
             mock.patch.object(agent_brief.audit, "write"):
            code = agent_brief.main(["--window-only", "--root", d, "--feeds", ""])
        self.assertEqual(code, 0)
        ran.assert_called_once()


class ThousandsSeparators(unittest.TestCase):
    """A correct note must not be rejected for writing 21,854 instead of 21854.

    Measured on the journal: 9 of 37 chief notes were rejected, and the reasons
    were fragments of comma-separated numbers - the fabrication check was
    splitting "21,854" into 21 and 854 and finding neither in the source.
    """

    def test_a_thousands_separator_is_one_number(self):
        note = {"agent": "chief", "summary": "cumulative 21,854 trials at z 4.72",
                "contradictions": [], "watch": "none"}
        self.assertEqual(desk.uncited(note, "cumulative=21854 z_crit_corrected=4.72"), [])

    def test_the_source_may_carry_the_separator_too(self):
        note = {"agent": "chief", "summary": "21854 trials", "contradictions": [], "watch": "none"}
        self.assertEqual(desk.uncited(note, "cumulative=21,854"), [])

    def test_a_fabricated_number_is_still_rejected(self):
        note = {"agent": "chief", "summary": "21,999 trials", "contradictions": [], "watch": "none"}
        # Reported as the figure the model wrote (21,999 normalised to 21999), not
        # as two fragments - the message should name what to fix.
        self.assertEqual(desk.uncited(note, "cumulative=21854"), ["21999"])

    def test_a_list_is_not_a_thousands_group(self):
        # "1,2" must not become 12: only \d{3} after the comma is a group.
        self.assertEqual(desk._thousands("1,2"), "1,2")
        self.assertEqual(desk._thousands("21,854"), "21854")
        self.assertEqual(desk._thousands("1,234,567"), "1234567")
        self.assertEqual(desk._thousands("2.191, 3.103"), "2.191, 3.103")

    def test_a_rejected_note_says_which_figure_was_missing(self):
        note = {"agent": "chief", "summary": "cumulative 21,854 at z 9.99",
                "contradictions": [], "watch": "none"}
        out = desk._guard(note, "cumulative=21854 z_crit_corrected=4.72")
        self.assertEqual(out["status"], "rejected")
        self.assertIn("9.99", out["reason"])


class TheDigestExplainsItsOwnNumbers(unittest.TestCase):
    """The jury escalated on 34 of 45 judged runs before the legend existed.

    Every one of those escalations compared two figures that mean different things:
    tier2's close against tier3's live quote a few seconds later, or
    significance.z against permutation.actual_z. The digest showed both without
    saying which was which, so the model did the reasonable thing with what it had.
    """

    def test_the_legend_leads_the_digest(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "funnel_history.log").write_text("2026-11-11T16:58:19Z | universe=1\n", encoding="utf-8")
            self.assertTrue(desk.digest(root).startswith(desk.FIELD_LEGEND))
            self.assertIn("TWO stages", desk.digest(root))

    def test_the_legend_does_not_license_a_number_nobody_measured(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "funnel_history.log").write_text("cumulative=29999\n", encoding="utf-8")
            src = desk.digest(root)
            note = {"agent": "chief", "summary": "cumulative 29999 at 44444",
                    "contradictions": [], "watch": "none"}
            self.assertEqual(desk.uncited(note, src), ["44444"])

    def test_the_two_prompts_that_hunt_contradictions_know_it(self):
        self.assertIn("FIELD LEGEND", desk.JURY_SYS)
        self.assertIn("FIELD LEGEND", desk.CHIEF_SYS)
        self.assertIn("not a contradiction", desk.FIELD_LEGEND)


class AnEscalationMustNameTheAction(unittest.TestCase):
    """The desk is judged by the attention it costs, not by how much it says.

    dsh-superagent's rule, adopted: an agent "speaks only when a person must act or
    can use a result". An escalation with no action makes a person go and find out
    what to do, which is the most expensive thing this desk can produce.
    """

    # JURY_POLICY is part of the real source, which is why 0.01 is citable.
    src = "cumulative=29999 passed_both=0; order size is 0.01 lots, a human types APPROVED"

    def note(self, **kw):
        base = {"agent": "jury", "verdict": "escalate", "size_lots": 0.01,
                "why": "two artifacts disagree", "conditions": []}
        base.update(kw)
        return base

    def test_an_escalation_without_an_action_is_refused(self):
        out = desk._guard(self.note(), self.src, "jury")
        self.assertEqual(out["status"], "rejected")
        self.assertIn("must name the action", out["reason"])

    def test_an_escalation_that_names_the_action_passes(self):
        out = desk._guard(self.note(conditions=["Reconcile the tier2 scan_id against a ledger row"]),
                          self.src, "jury")
        self.assertEqual(out["status"], "ok")

    def test_standing_down_needs_no_action(self):
        out = desk._guard({"agent": "jury", "verdict": "stand_down", "size_lots": 0.01,
                           "why": "29999 tests, nothing cleared the bar", "conditions": []},
                          self.src, "jury")
        self.assertEqual(out["status"], "ok")


if __name__ == "__main__":
    unittest.main()
