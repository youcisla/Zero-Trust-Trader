"""The desk: LLM roles that read the tree and write notes.

Design rule, and it is the whole point of this module: **an agent may not
introduce a number.** Every figure a note contains must already appear in the
artifact the agent was handed; a note that invents one is rejected before it is
written. That is what makes an LLM safe to put next to a quantitative pipeline —
it cannot manufacture a z-score, a win rate or a trial count, and it cannot
launch, size, veto or close anything. `AGENTS.md` §1 still owns execution:
human `APPROVED` plus the bridge's single-use token.

Roles, and why only these four are agents:

| role    | does                                            | why a model and not code |
| ------- | ----------------------------------------------- | ------------------------ |
| scout   | reads headlines, maps them to symbols           | text, not numbers        |
| skeptic | argues against a candidate using its own stats   | adversarial prose        |
| chief   | explains the run, names contradictions           | synthesis                |
| jury    | reads the facts, the objection and the summary    | the last voice before a human; `size_lots` policed to 0.01 |

Chart-reading, strategy testing and execution stay deterministic on purpose:
tier 2 and tier 3 already do that work with a measured error rate, and an agent
in the order path is the thing this repository exists to prevent.

Stdlib only — urllib for HTTP, xml.etree for RSS. No SDK, no new dependency.
"""

from __future__ import annotations

import json
import math
import os
import re
import urllib.error
import urllib.request
from urllib.parse import urlsplit
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from xml.etree import ElementTree

# This account serves deepseek-flash for the deepseek-chat alias - the drift
# check proved it on the first live run (asked deepseek-chat, served
# deepseek-flash). Pin what is actually served, so the drift flag keeps meaning
# "something changed under us" instead of firing on every run.
DEFAULT_MODEL = "deepseek-flash"
RULES = "DESK.md"
JOURNAL = "desk_journal.jsonl"
DIGEST_LIMIT = 6000   # characters of each artifact the model is shown
DEFAULT_URL = "https://api.deepseek.com/chat/completions"
NOTES = "agent_notes.json"
MAX_RESPONSE = 256 * 1024
MAX_ARTIFACT = 2 * 1024 * 1024


class DeskError(RuntimeError):
    pass


def api_key(root: Path | None = None) -> str | None:
    """Env first, then a `.env` next to the tree. Never logged, never written."""
    k = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if k:
        return k
    for candidate in ((root or Path.cwd()) / ".env",):
        try:
            for line in candidate.read_text(encoding="utf-8").splitlines():
                if line.strip().startswith("DEEPSEEK_API_KEY="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'") or None
        except OSError:
            continue
    return None


def openrouter_key(root: Path | None = None) -> str | None:
    """OpenRouter's key, needed only by the decision-model layer.

    Same rule as `api_key`: environment first, then a local `.env`. Absent is not an
    error - the layer records "not configured" and the desk runs without it.
    """
    k = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if k:
        return k
    try:
        for line in ((root or Path.cwd()) / ".env").read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("OPENROUTER_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'") or None
    except OSError:
        pass
    return None


JEV_URL = "https://openrouter.ai/api/alpha/decisions"
JEV_MODEL = "typesafe/jev-1.14"
# The questions are typed on purpose: a decision model answers from a fixed set and
# returns probabilities, so it cannot invent a sentence or a number. They ask the
# same thing the jury decides, which is what makes the two outputs comparable.
JEV_QUESTIONS = {
    "attention": {
        "type": "choice",
        "instructions": "Given this scan and the desk's reading of it, what deserves a person's attention now?",
        "criteria": {
            "show": "one candidate is worth a human's attention, with its own numbers",
            "stand_down": "nothing here deserves a human's attention; the gate refused everything",
            "escalate": "a person must act before anything else happens",
        },
    },
    "grounded": {
        "type": "choice",
        "instructions": "Is the desk's verdict supported by the figures it cites, with no figure it invented?",
        "criteria": {
            "supported": "every claim follows from the figures in the state, and nothing is contradicted",
            "unsupported": "the verdict cites a figure absent from the state, contradicts one, or answers a different question",
        },
    },
}


def jev(facts: str, desk_view: dict, key: str | None, *, url: str = JEV_URL,
        timeout: int = 30) -> dict:
    """A typed decision on the same evidence the jury saw.

    Advisory only: it returns a label and probabilities, never prose, and nothing in
    this file reads its answer back into a verdict, a size or a gate. `status` is
    `ok`, `not configured` or `error` - a decision model that is not reachable is a
    visible absence, not a silent zero.
    """
    if not key:
        return {"status": "not configured", "model": JEV_MODEL,
                "reason": "OPENROUTER_API_KEY is not set (env or .env)",
                "would_ask": sorted(JEV_QUESTIONS)}
    payload = {"model": JEV_MODEL, "state": facts[-12000:] + "\n\nDESK VIEW:\n"
               + json.dumps(desk_view, ensure_ascii=False)[:4000],
               "questions": JEV_QUESTIONS}
    try:
        doc = _post(payload, key, url=url, timeout=timeout)
    except DeskError as e:
        return {"status": "error", "model": JEV_MODEL, "reason": str(e)}
    answers = doc.get("answers") or {}
    out = {"status": "ok", "model": doc.get("model") or JEV_MODEL,
           "usage": doc.get("usage") or {}, "answers": {}}
    for name in JEV_QUESTIONS:
        a = answers.get(name) or {}
        # Only the typed fields are copied: a provider that added prose to the
        # response does not get to smuggle it into the record.
        out["answers"][name] = {
            k: a[k] for k in ("type", "choice", "probabilities", "confidence") if k in a
        } or {"missing": True}
    return out


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect is how a trusted URL becomes an untrusted one on the next hop."""

    def redirect_request(self, *args, **kwargs):
        raise DeskError("redirect refused")


def _post(payload: dict, key: str, url: str = DEFAULT_URL, timeout: int = 90) -> dict:
    parsed = urlsplit(url)
    if parsed.username or parsed.password or not parsed.hostname:
        raise DeskError("model endpoint must not contain credentials and must have a host")
    if parsed.scheme != "https" and not (
            parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}):
        raise DeskError("model endpoint requires HTTPS; HTTP is reserved for loopback test fixtures")
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        method="POST",
    )
    try:
        # Hard cap: a desk role must not hold the run for 90 s. The caller's timeout
        # can only lower it, which is the point - the ceiling is not negotiable.
        with urllib.request.build_opener(_NoRedirect()).open(req, timeout=min(timeout, 30)) as r:
            raw = r.read(MAX_RESPONSE + 1)
            if len(raw) > MAX_RESPONSE:
                raise DeskError("model endpoint response exceeds the size limit")
            return json.loads(raw.decode())
    except urllib.error.HTTPError as e:
        raise DeskError(f"HTTP {e.code} from the model endpoint") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise DeskError(f"model endpoint unreachable: {e}") from e
    except (ValueError, UnicodeError) as e:
        raise DeskError("model endpoint returned invalid JSON") from e


_RULES_CACHE: dict[str, str] = {}


def rules(root: Path | None = None) -> str:
    """DESK.md, read fresh per process. Missing file is not fatal — the guard is
    the gate, not the prose — but it is reported so the reader knows."""
    key = str(root or Path.cwd())
    if key not in _RULES_CACHE:
        try:
            _RULES_CACHE[key] = (Path(key) / RULES).read_text(encoding="utf-8")[:4000]
        except OSError:
            _RULES_CACHE[key] = ("DESK RULES (built-in fallback): never invent a number; "
                                 "quote only figures present in the artifact; state missing data as missing.")
    return _RULES_CACHE[key]


def _with_rules(messages: list[dict], root: Path | None) -> list[dict]:
    return [{"role": "system", "content": "DESK RULES — read before answering:\n\n" + rules(root)},
            *messages]


def chat(messages: list[dict], key: str, *, model: str | None = None,
         url: str | None = None, max_tokens: int = 4000, timeout: int = 90,
         root: Path | None = None, meta: dict | None = None) -> dict:
    """One JSON-mode completion. temperature 0: this is not a creative task."""
    url = url or os.environ.get("ZTT_AGENT_URL", DEFAULT_URL)
    payload = {
        "model": model or os.environ.get("ZTT_AGENT_MODEL", DEFAULT_MODEL),
        "messages": _with_rules(messages, root),
        "temperature": 0,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
    }
    data = _post(payload, key, url, timeout)
    if not isinstance(data, dict):
        raise DeskError("unexpected response envelope from model endpoint")
    if meta is not None:
        # The provider names the model it actually served. Record it: a silent
        # upgrade changes what every note means and nothing else would show it.
        served = data.get("model")
        usage = data.get("usage")
        if not isinstance(served, str) or not served.strip():
            raise DeskError("model endpoint did not identify the served model")
        if usage is not None and not isinstance(usage, dict):
            raise DeskError("model endpoint returned invalid usage metadata")
        # Only the two counts this code sums are constrained. The provider grew
        # `prompt_tokens_details` / `completion_tokens_details` (nested objects),
        # and a whole-response "every value is a non-negative int" rule turned
        # that harmless addition into every role erroring out at once.
        for field in ("prompt_tokens", "completion_tokens"):
            if field in usage and (isinstance(usage[field], bool)
                                   or not isinstance(usage[field], int) or usage[field] < 0):
                raise DeskError("model endpoint returned invalid usage metadata")
        meta["model"] = served
        meta["usage"] = usage
    try:
        choice = data["choices"][0]
        text = choice["message"]["content"]
        if not isinstance(text, str):
            raise TypeError("content must be text")
    except (KeyError, IndexError, TypeError) as e:
        raise DeskError("unexpected response shape from model endpoint") from e
    return _object(text, choice.get("finish_reason"), max_tokens)


def _object(text: str, finish: str | None = None, cap: int = 0) -> dict:
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\n?", "", t)
        t = re.sub(r"\n?```$", "", t).strip()
    try:
        out = json.loads(t)
    except json.JSONDecodeError as e:
        if finish == "length":
            raise DeskError(
                f"response truncated at max_tokens={cap} (finish_reason=length); "
                f"the note is incomplete, not malformed") from e
        raise DeskError(f"model did not return JSON: {t[:200]}") from e
    if not isinstance(out, dict):
        raise DeskError("model returned JSON that is not an object")
    return out


# ---------------------------------------------------------------- citations

# A number is only a claim if it is a standalone numeric token. Three things
# that used to be counted as claims and should not be:
#   \u2014    json.dumps escapes the em dash, and -?\d+ matched the "2014" inside
#             it, so every note written with a dash was rejected for inventing 2014
#   2026-11-09  a date's "-09" is not a negative number
#   150-vs-129  a hyphen between words is punctuation, not a sign
_NUM = re.compile(r"(?<![\w.])[+-]?(?:\d+(?:\.\d+)?|\.\d+)(?:[eE][+-]?\d+)?(?![\d.])")
_DATE_TIME = re.compile(
    r"\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:\d{2})?)?"
    r"|(?<!\d)\d{2}:\d{2}(?::\d{2})?Z?(?!\d)")


def _thousands(text: str) -> str:
    """`21,854` is one number, not two.

    The scanner splits on the comma, so a correct note that writes "cumulative
    21,854" used to fail the fabrication check with `uncited numbers: 21, 854` —
    measured at 9 of 37 chief notes. Only digit-group commas are removed: a list
    ("1,2") and a European decimal ("2.191") are left exactly as written.
    """
    return re.sub(r"(?<=\d),(?=\d{3}(?!\d))", "", text)


def _renders(v: float) -> set[str]:
    out = {f"{v}", f"{v:.1f}", f"{v:.2f}", f"{v:.3f}", f"{v:g}", f"{int(v)}" if float(v).is_integer() else f"{v}"}
    return {s.lstrip("+") for s in out}


def uncited(note: dict, source: str) -> list[str]:
    """Every number in the note that is not in the source it was shown.

    FORMAT: "4.53" is accepted when 4.531 is in the source (rounded render), but
    a number that appears nowhere is a fabrication and fails the whole note.
    """
    bad = []
    # ensure_ascii=False keeps em dashes as characters; dates are not figures.
    blob = _thousands(_DATE_TIME.sub(" ", json.dumps(note, ensure_ascii=False, default=str)))
    source_tokens = [m.group(0) for m in _NUM.finditer(_thousands(_DATE_TIME.sub(" ", source)))]
    allowed = set()
    for token in source_tokens:
        value = float(token)
        if math.isfinite(value):
            allowed.update(Decimal(v) for v in _renders(value) if Decimal(v) != 0 or value == 0)
    for m in _NUM.finditer(blob):
        raw = m.group(0)
        try:
            v = float(raw)
        except ValueError:
            continue
        if not math.isfinite(v) or Decimal(raw) not in allowed:
            bad.append(raw)
    return sorted(set(bad))


def _validate_note(note: dict, source: str, expected_role: str | None) -> str | None:
    role = note.get("agent")
    schemas = {
        "chief": ({"agent", "summary", "contradictions", "watch"}, {"summary"}),
        "skeptic": ({"agent", "symbol", "verdict", "reasons", "what_would_change_my_mind"},
                    {"symbol", "verdict", "reasons"}),
        "scout": ({"agent", "items", "caveat"}, {"items"}),
        # The judge returns a verdict, not a size. `size_lots` is accepted only
        # so the note can restate the policy figure; anything else is rejected.
        "jury": ({"agent", "verdict", "size_lots", "why", "conditions"}, {"verdict", "why"}),
    }
    if role not in schemas or (expected_role and role != expected_role):
        return "missing or incorrect advisory role"
    allowed, required = schemas[role]
    if set(note) - allowed or not required <= note.keys():
        return "invalid advisory fields"
    if len(json.dumps(note, ensure_ascii=False)) > 16000:
        return "advisory exceeds the size limit"
    for name in ("summary", "watch", "symbol", "verdict", "caveat", "what_would_change_my_mind", "why"):
        if name in note and (not isinstance(note[name], str) or len(note[name]) > 4000):
            return f"invalid {name}"
        if name in required and isinstance(note.get(name), str) and not note[name].strip():
            return f"empty {name}"
    for name in ("contradictions", "reasons", "conditions"):
        if name in note and (not isinstance(note[name], list) or len(note[name]) > 3 or
                any(not isinstance(v, str) or not v.strip() or len(v) > 4000 for v in note[name])):
            return f"invalid {name}"
    if role == "skeptic" and (note["verdict"] not in {"noise", "not_proven"} or not note["reasons"]):
        return "invalid skeptic verdict or reasons"
    if role == "chief" and len(note["summary"].split()) > 120:
        return "chief summary exceeds the word limit"
    if role == "jury":
        if note["verdict"] not in {"stand_down", "escalate", "propose"}:
            return "invalid jury verdict"
        # Sizing is policy, not opinion. A judge that can pick a size is the one
        # thing this desk must never contain; the geometric check is deliberate.
        size = note.get("size_lots", 0.01)
        if isinstance(size, bool) or not isinstance(size, (int, float)) or abs(float(size) - 0.01) > 1e-9:
            return "jury may not size an order"
        # Attention is the currency (dsh-superagent's rule, and this desk's real
        # failure): an escalation that does not name an action asks a person to
        # go and find out what to do. Name it, or stand down.
        if note["verdict"] == "escalate" and not note.get("conditions"):
            return "an escalation must name the action a person takes"
    if role == "scout":
        symbols = set(re.findall(r'"symbol"\s*:\s*"([^"\\]+)"', source))
        if not isinstance(note["items"], list) or len(note["items"]) > 20:
            return "invalid scout items"
        for item in note["items"]:
            if not isinstance(item, dict) or set(item) != {"headline", "source", "symbols", "stance", "why"}:
                return "invalid scout item fields"
            if any(not isinstance(item[k], str) or not item[k].strip() or len(item[k]) > 4000
                   for k in ("headline", "source", "stance", "why")):
                return "invalid scout item text"
            if item["stance"] not in {"risk_on", "risk_off", "neutral"}:
                return "invalid scout stance"
            if not isinstance(item["symbols"], list) or len(item["symbols"]) > 3 or any(
                    not isinstance(v, str) or v not in symbols for v in item["symbols"]):
                return "scout named an unknown symbol"
    return None


def _guard(note: dict, source: str, expected_role: str | None = None) -> dict:
    invalid = _validate_note(note, source, expected_role)
    if invalid:
        return {"status": "rejected", "reason": invalid, "agent": note.get("agent")}
    bad = uncited(note, source)
    if bad:
        return {"status": "rejected", "reason": "uncited numbers: " + ", ".join(bad[:6]),
                "agent": note.get("agent"), "raw": note}
    return {**note, "status": "ok"}


# ---------------------------------------------------------------- the facts

FIELD_LEGEND = """FIELD LEGEND - read this before calling anything a contradiction.
It is not a contradiction if a line here explains it:
- ONE scan, TWO stages. tier2 wrote all_hits at the signal; tier3 re-read the live
  quote seconds later into current. A close in all_hits and a reference_bid in
  current are the same instrument at two moments: a few ticks apart is a clock.
- DEDUPE. tier2.deduped is the LIST of pairs removed on this scan; the ledger's
  deduped=N is that list's length, so an empty list and deduped=0 agree. Both vary
  scan to scan; a difference between scans is not a contradiction.
- "z" NAMES TWO STATISTICS. significance.z is the win-rate z; significance.t_stat_R
  is the mean-net-R statistic; permutation.actual_z repeats t_stat_R as the
  permutation saw it. Only t_stat_R is compared with z_crit_bonferroni.
- ENGINE generated_utc is when the supervisor last wrote and may be older than the
  scan: an idle supervisor, not a disagreement.
"""


def digest(root: Path) -> str:
    """The only thing an agent is allowed to know. Text, because it is audited."""
    parts = [FIELD_LEGEND]
    ledger = root / "funnel_history.log"
    if ledger.exists():
        try:
            with ledger.open("rb") as stream:
                size = stream.seek(0, 2)
                stream.seek(max(0, size - 16384))
                if size > 16384:
                    stream.readline()
                rows = [line for line in stream.read(16384).decode("utf-8", errors="replace").splitlines()
                        if line.strip()]
            parts.append("LAST LEDGER ROWS:\n" + "\n".join(rows[-5:]))
        except OSError:
            parts.append("LEDGER: present but unreadable")
    for name in ("tier3_results.json", "tier2_candidates.json"):
        p = root / name
        if p.exists():
            try:
                if p.stat().st_size > MAX_ARTIFACT:
                    raise ValueError("artifact exceeds read limit")
                blob = json.dumps(json.loads(p.read_text(encoding="utf-8")), indent=1)
                age = max(0, int(datetime.now(timezone.utc).timestamp() - p.stat().st_mtime))
                parts.append(f"{name} observation age_s={age}; historical context, not an execution instruction")
                if len(blob) > DIGEST_LIMIT:
                    # Say it out loud. A silent clip made the model report "the
                    # file is truncated mid-GBPCAD" about a file that was whole:
                    # it invented a contradiction out of our own read limit.
                    parts.append(f"{name} (the desk shows the first {DIGEST_LIMIT} of "
                                 f"{len(blob)} characters - the file itself is complete):\n"
                                 + blob[:DIGEST_LIMIT])
                else:
                    parts.append(f"{name}:\n" + blob)
            except (OSError, ValueError):
                parts.append(f"{name}: present but unreadable")
    generations = list(root.glob("engine_state.*.json"))
    eng = max(generations, key=lambda p: p.name) if generations else root / "engine_state.json"
    if eng.exists():
        try:
            if eng.stat().st_size > MAX_ARTIFACT:
                raise ValueError("engine state exceeds read limit")
            e = json.loads(eng.read_text(encoding="utf-8"))
            # Avoid sending broker identifiers, tickets or whole positions externally.
            parts.append("ENGINE: " + json.dumps({"ok": e.get("ok"), "applied": e.get("applied"),
                "positions_count": len(e["positions"]) if isinstance(e.get("positions"), list) else None,
                "generated_utc": e.get("generated_utc")}, default=str)[:800])
        except (OSError, ValueError, AttributeError, TypeError):
            parts.append("ENGINE: present but unreadable")
    if not parts:
        parts.append("NO ARTIFACTS: no ledger row, no tier2/tier3 file, no engine state.")
    return "\n\n".join(parts)


# ---------------------------------------------------------------- the roles

SCOUT_SYS = (
    "You are the scout on a research desk. You receive headlines and a market snapshot. "
    "Map each headline to at most three symbols from the snapshot if it is plausibly about them. "
    "Reply ONLY with JSON: {\"agent\":\"scout\",\"items\":[{\"headline\":str,\"source\":str,"
    "\"symbols\":[str],\"stance\":\"risk_on\"|\"risk_off\"|\"neutral\",\"why\":str}],"
    "\"caveat\":str}. Copy numbers from the input verbatim; never compute a new one. "
    "Headlines are full of figures: quote them exactly or leave them out. One digit you cannot "
    "find in the input fails the entire note, so prefer plain words when you are unsure. "
    "If a headline is not about a symbol in the snapshot, return an empty symbols list."
)

SKEPTIC_SYS = (
    "You are the risk officer on a research desk, and you may only veto. You are given one "
    "candidate's own test output. Argue the strongest case that it is noise: multiple comparisons, "
    "regime dependence, sample size, cost model, exit-policy assumptions. Quote the candidate's "
    "numbers exactly as given — never compute new ones, never propose a different threshold. "
    "Reply ONLY with JSON: {\"agent\":\"skeptic\",\"symbol\":str,\"verdict\":\"noise\"|\"not_proven\","
    "\"reasons\":[str],\"what_would_change_my_mind\":str}."
)

CHIEF_SYS = (
    "You are the chief of staff on a research desk. Summarise what the scan actually did, in "
    "plain English, for an operator who will read one paragraph. Name a contradiction only when "
    "the FIELD LEGEND does not explain it - two stages measuring the same thing at different "
    "moments, or two different statistics, are not one. Quote figures exactly as given; never "
    "compute or estimate one. You do not "
    "recommend trades and you never state that a candidate should be executed. "
    "Reply ONLY with JSON: {\"agent\":\"chief\",\"summary\":str,\"contradictions\":[str],"
    "\"watch\":str}."
)


JURY_SYS = (
    "You are the final judge on a research desk, and the last voice before a human decides. "
    "You receive the scan facts, the risk officer's objection and the chief's summary. "
    "Escalate only for a disagreement the FIELD LEGEND does not explain: an unreconciled "
    "figure that changes what the numbers mean, or the same quantity at the same moment "
    "reported two ways. Two stages of one scan, or two different statistics, are not that. "
    "Return the desk's verdict for today: \"stand_down\" (nothing here is worth a human's time), "
    "\"escalate\" (a person must act, and you can name the action in one line) or \"propose\" (one "
    "candidate is worth showing to the operator). "
    "One rule decides escalate: someone must act and you can say what. If you cannot name the "
    "action, stand down - this desk is judged by the attention it costs, not by how much it says. You never place, approve or cancel an order: a human types "
    "APPROVED and an independent consent gate issues the token. The order size is fixed by policy at "
    "0.01 lots and is not yours to choose. Quote figures exactly as given; never compute one. "
    "Reply ONLY with JSON: {\"agent\":\"jury\",\"verdict\":\"stand_down\"|\"escalate\"|\"propose\","
    "\"size_lots\":0.01,\"why\":str,\"conditions\":[str]}. "
    "On escalate, conditions holds the action itself, imperative and one line."
)

JURY_POLICY = ("POLICY, not a suggestion and not yours to change: order size is 0.01 lots, the human "
               "types APPROVED, no agent holds a token.")


def jury(facts: str, skeptic_note: dict, chief_note: dict, key: str,
         root: Path | None = None, meta: dict | None = None, **kw) -> dict:
    source = facts + json.dumps(skeptic_note) + json.dumps(chief_note) + JURY_POLICY
    note = chat([{"role": "system", "content": JURY_SYS},
                 {"role": "user", "content": facts[-5000:]
                  + "\n\nRISK OFFICER:\n" + json.dumps(skeptic_note, indent=1)[:2500]
                  + "\n\nCHIEF:\n" + json.dumps(chief_note, indent=1)[:2500]
                  + "\n\n" + JURY_POLICY
                  + "\n\nOne verdict, at most three conditions, under 100 words."}],
                key, root=root, meta=meta, **kw)
    note.setdefault("size_lots", 0.01)
    return _guard(note, source, "jury")


def scout(headlines: list[dict], facts: str, key: str, root: Path | None = None, meta: dict | None = None, **kw) -> dict:
    payload = "SNAPSHOT:\n" + facts + "\n\nHEADLINES:\n" + json.dumps(headlines[:40], indent=1)
    note = chat([{"role": "system", "content": SCOUT_SYS},
                 {"role": "user", "content": payload}], key, root=root, meta=meta, **kw)
    return _guard(note, facts + json.dumps(headlines), "scout")


def skeptic(results: list[dict], facts: str, key: str, root: Path | None = None, meta: dict | None = None, **kw) -> dict:
    if not results:
        return {"agent": "skeptic", "status": "no candidate", "verdict": "no candidate",
                "reason": "tier 3 has no results on disk"}
    top = results[0]
    note = chat([{"role": "system", "content": SKEPTIC_SYS},
                 {"role": "user", "content": "CANDIDATE:\n" + json.dumps(top, indent=1)[:4000]
                  + "\n\nCONTEXT (do not restate any of it):\n" + facts[-4000:]
                  + "\n\nAt most 3 reasons, one sentence each."}], key, root=root, meta=meta, **kw)
    note.setdefault("symbol", top.get("symbol"))
    return _guard(note, facts + json.dumps(top), "skeptic")


def chief(facts: str, key: str, root: Path | None = None, meta: dict | None = None, **kw) -> dict:
    note = chat([{"role": "system", "content": CHIEF_SYS},
                 {"role": "user", "content": facts
                  + "\n\nWrite one paragraph, under 120 words. Do not restate the rows."}],
                key, root=root, meta=meta, **kw)
    return _guard(note, facts, "chief")


# ---------------------------------------------------------------- inputs

MAX_FEED = 512 * 1024


def headlines(feeds: list[str], limit: int = 20) -> list[dict]:
    """RSS/Atom, stdlib only. A dead feed is reported, not silently skipped.

    ElementTree never fetches external entities, and the two remaining XML
    attacks are closed by hand rather than by a new dependency: the body is
    capped at 512 KiB and any document declaring a DTD or an entity is refused.
    That kills billion-laughs and any XXE payload outright.
    """
    out, errors = [], []
    for url in feeds[:5]:
        if len(out) >= limit:
            break
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "zero-trust-trader/1.0"})
            with urllib.request.build_opener(_NoRedirect()).open(req, timeout=20) as r:
                raw = r.read(MAX_FEED + 1)
            if len(raw) > MAX_FEED:
                raise ValueError("feed exceeds size limit")
            text = raw.decode("utf-8-sig")
            if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
                raise ValueError("feed declares a DTD — refused")
            tree = ElementTree.fromstring(text)
        except Exception as e:  # noqa: BLE001 - a feed failing must not kill the brief
            errors.append(f"{url}: {type(e).__name__}")
            continue
        for item in tree.iter():
            if item.tag.split("}")[-1] not in ("item", "entry"):
                continue
            title = (item.findtext("title") or item.findtext("{*}title") or "").strip()
            if not title:
                continue
            out.append({"headline": title[:300], "source": url})
            if len(out) >= limit:
                break
    if errors:
        out.append({"headline": "FEED ERROR: " + "; ".join(errors), "source": "desk"})
    return out


def run(root: Path, feeds: list[str] | None = None, model: str | None = None) -> dict:
    """Produce the desk's notes. Never raises on a model failure — it records it."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    facts = digest(root)
    feeds = feeds or []
    notes = {"generated_utc": now, "model": model or os.environ.get("ZTT_AGENT_MODEL", DEFAULT_MODEL),
             "facts_sha": _sha(facts), "roles": {}}

    key = api_key(root)
    if not key:
        notes["status"] = "no key"
        notes["hint"] = "set DEEPSEEK_API_KEY (env or .env); nothing was called"
        for role in ("scout", "skeptic", "chief", "jury"):
            notes["roles"][role] = {"agent": role, "status": "skipped: no key"}
        return notes

    kw = {"model": model} if model else {}
    meta: dict = {}
    role_meta: dict = {}
    t3 = {}
    try:
        t3 = json.loads((root / "tier3_results.json").read_text(encoding="utf-8"))
        if not isinstance(t3, dict):
            t3 = {}
    except (OSError, ValueError):
        t3 = {}

    if feeds:
        # The scout reads raw news, so it is the only role that sees figures it can
        # accidentally paraphrase. The guard is right to reject the note; asking a
        # second time is cheaper than publishing a desk with one voice missing.
        heads = headlines(feeds)
        for attempt in (1, 2):
            meta = {}
            try:
                note = scout(heads, facts, key, root, meta, **kw)
            except DeskError as e:
                note = {"agent": "scout", "status": "error", "reason": str(e)}
            if note.get("status") != "rejected" or attempt == 2:
                break
        notes["roles"]["scout"] = note
        role_meta["scout"] = dict(meta)
    else:
        notes["roles"]["scout"] = {"agent": "scout", "status": "no feed configured",
                                   "reason": "add feeds to desk.toml: news_feeds = [\"https://...\"]"}

    for role, fn, arg in (("skeptic", skeptic, (t3.get("results") or [], facts)),
                          ("chief", chief, (facts,))):
        try:
            meta = {}
            notes["roles"][role] = fn(*arg, key, root, meta, **kw)
        except DeskError as e:
            notes["roles"][role] = {"agent": role, "status": "error", "reason": str(e)}
        role_meta[role] = dict(meta)

    try:
        meta = {}
        notes["roles"]["jury"] = jury(facts, notes["roles"].get("skeptic") or {},
                                      notes["roles"].get("chief") or {}, key, root, meta, **kw)
    except DeskError as e:
        notes["roles"]["jury"] = {"agent": "jury", "status": "error", "reason": str(e)}
    role_meta["jury"] = dict(meta)

    # Third layer: a System One model answers two typed questions on the same
    # evidence. Advisory and recorded beside the verdict - nothing below reads it.
    verdict_note = notes["roles"].get("jury") or {}
    try:
        notes["jev"] = jev(facts, {"verdict": verdict_note.get("verdict"),
                                   "why": verdict_note.get("why") or ""},
                           openrouter_key(root))
    except Exception as e:  # a layer that fails must not take the desk with it
        notes["jev"] = {"status": "error", "model": JEV_MODEL, "reason": str(e)}

    # `meta` after the loop is the jury's call, or {} if the jury errored - so
    # "what model actually served" used to be empty or last-caller-biased even
    # when three roles answered. Derive it from every role's metadata instead.
    # One answer, not the first role's: if the four calls were served by different
    # models, "served" has no single value and the per-role map below is the record.
    models = {m for item in role_meta.values() if (m := item.get("model"))}
    served = models.pop() if len(models) == 1 else None
    notes["model_returned"] = served
    notes["usage"] = next((item.get("usage") for item in role_meta.values()
                           if item.get("usage")), None)
    notes["role_metadata"] = role_meta
    notes["usage_total"] = {
        field: sum(value for item in role_meta.values()
                   for value in [(item.get("usage") or {}).get(field, 0)]
                   if isinstance(value, int) and not isinstance(value, bool) and value >= 0)
        for field in ("prompt_tokens", "completion_tokens")
    }
    notes["model_drift_roles"] = {role: item.get("model") for role, item in role_meta.items()
                                  if item.get("model") and item["model"] != notes["model"]}
    if served and served != notes["model"]:
        # Pinning only works if a mismatch is visible. A silent alias upgrade
        # makes every future note a different instrument than the one measured.
        notes["model_drift"] = {"asked": notes["model"], "served": served}

    statuses = [r.get("status") for r in notes["roles"].values()]
    accepted = [st for st in statuses if st in (None, "ok")]
    errored = [st for st in statuses if st == "error"]
    rejected = [st for st in statuses if st == "rejected"]
    # An exit code, or a role that returned nothing, is not a brief. Reporting
    # "ok" because nothing was *rejected* is the failure this whole repo avoids.
    if rejected:
        notes["status"] = "rejected"
    elif errored and not accepted:
        notes["status"] = "error"
    elif errored:
        notes["status"] = "partial"
    else:
        notes["status"] = "ok"
    return notes


def _sha(text: str) -> str:
    import hashlib
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def append_journal(root: Path, notes: dict) -> Path:
    """One line per brief, forever. The notes file is overwritten every run; the
    journal is what makes "compare this week to last" possible — and it is where
    a rejected note's reasons survive."""
    role_state = {k: v.get("status", "ok") for k, v in (notes.get("roles") or {}).items()}
    chief = (notes.get("roles") or {}).get("chief") or {}
    line = {
        "ts": notes.get("generated_utc"),
        "status": notes.get("status"),
        "model": notes.get("model"),
        "model_returned": notes.get("model_returned"),
        "drift": bool(notes.get("model_drift")),
        "facts_sha": notes.get("facts_sha"),
        "roles": role_state,
        "chief": (chief.get("summary") or "")[:400],
        "rejected": [v.get("reason") for v in (notes.get("roles") or {}).values()
                     if v.get("status") == "rejected"],
        # A run that failed left no trace of *why*: the audit said "error" twelve
        # times in a row and answering it meant re-reading the live file. The
        # reasons belong in the record.
        "reasons": {k: v.get("reason") for k, v in (notes.get("roles") or {}).items()
                    if v.get("status") == "error" and v.get("reason")},
    }
    path = root / JOURNAL
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(line, default=str) + "\n")
    return path


def write_notes(root: Path, notes: dict) -> Path:
    p = root / NOTES
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(notes, indent=1), encoding="utf-8")
    tmp.replace(p)
    return p
