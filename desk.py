"""The desk: LLM roles that read the tree and write notes.

Design rule, and it is the whole point of this module: **an agent may not
introduce a number.** Every figure a note contains must already appear in the
artifact the agent was handed; a note that invents one is rejected before it is
written. That is what makes an LLM safe to put next to a quantitative pipeline —
it cannot manufacture a z-score, a win rate or a trial count, and it cannot
launch, size, veto or close anything. `AGENTS.md` §1 still owns execution:
human `APPROVED` plus the bridge's single-use token.

Roles, and why only these three are agents:

| role    | does                                            | why a model and not code |
| ------- | ----------------------------------------------- | ------------------------ |
| scout   | reads headlines, maps them to symbols           | text, not numbers        |
| skeptic | argues against a candidate using its own stats   | adversarial prose        |
| chief   | explains the run, names contradictions           | synthesis                |

Chart-reading, strategy testing and execution stay deterministic on purpose:
tier 2 and tier 3 already do that work with a measured error rate, and an agent
in the order path is the thing this repository exists to prevent.

Stdlib only — urllib for HTTP, xml.etree for RSS. No SDK, no new dependency.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree

DEFAULT_MODEL = "deepseek-chat"
RULES = "DESK.md"
JOURNAL = "desk_journal.jsonl"
DEFAULT_URL = "https://api.deepseek.com/chat/completions"
NOTES = "agent_notes.json"


class DeskError(RuntimeError):
    pass


def api_key(root: Path | None = None) -> str | None:
    """Env first, then a `.env` next to the tree. Never logged, never written."""
    k = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if k:
        return k
    for candidate in ((root or Path.cwd()) / ".env", Path(".env")):
        try:
            for line in candidate.read_text(encoding="utf-8").splitlines():
                if line.strip().startswith("DEEPSEEK_API_KEY="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'") or None
        except OSError:
            continue
    return None


def _post(payload: dict, key: str, url: str = DEFAULT_URL, timeout: int = 90) -> dict:
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:300]
        raise DeskError(f"HTTP {e.code} from the model endpoint: {detail}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise DeskError(f"model endpoint unreachable: {e}") from e


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
         url: str | None = None, max_tokens: int = 1200, timeout: int = 90,
         root: Path | None = None) -> dict:
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
    try:
        text = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as e:
        raise DeskError(f"unexpected response shape: {str(data)[:200]}") from e
    return _object(text)


def _object(text: str) -> dict:
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\n?", "", t)
        t = re.sub(r"\n?```$", "", t).strip()
    try:
        out = json.loads(t)
    except json.JSONDecodeError as e:
        raise DeskError(f"model did not return JSON: {t[:200]}") from e
    if not isinstance(out, dict):
        raise DeskError("model returned JSON that is not an object")
    return out


# ---------------------------------------------------------------- citations

_NUM = re.compile(r"-?\d+(?:\.\d+)?")


def _renders(v: float) -> set[str]:
    out = {f"{v}", f"{v:.1f}", f"{v:.2f}", f"{v:.3f}", f"{v:g}", f"{int(v)}" if float(v).is_integer() else f"{v}"}
    return {s.lstrip("+") for s in out}


def uncited(note: dict, source: str) -> list[str]:
    """Every number in the note that is not in the source it was shown.

    FORMAT: "4.53" is accepted when 4.531 is in the source (rounded render), but
    a number that appears nowhere is a fabrication and fails the whole note.
    """
    bad = []
    for m in _NUM.finditer(json.dumps(note, default=str)):
        raw = m.group(0)
        try:
            v = float(raw)
        except ValueError:
            continue
        if v in (0.0, 1.0, 2.0, 3.0, 4.0, 5.0) and len(raw) == 1:
            continue  # counters and ordinals in prose ("two gates")
        if not any(r in source for r in _renders(v)):
            bad.append(raw)
    return sorted(set(bad))


def _guard(note: dict, source: str) -> dict:
    bad = uncited(note, source)
    if bad:
        return {"status": "rejected", "reason": "uncited numbers: " + ", ".join(bad[:6]),
                "agent": note.get("agent"), "raw": note}
    note["status"] = note.get("status", "ok")
    return note


# ---------------------------------------------------------------- the facts

def digest(root: Path) -> str:
    """The only thing an agent is allowed to know. Text, because it is audited."""
    parts = []
    ledger = root / "funnel_history.log"
    if ledger.exists():
        rows = [l for l in ledger.read_text(encoding="utf-8").splitlines() if l.strip()]
        parts.append("LAST LEDGER ROWS:\n" + "\n".join(rows[-5:]))
    for name in ("tier3_results.json", "tier2_candidates.json"):
        p = root / name
        if p.exists():
            try:
                parts.append(f"{name}:\n" + json.dumps(json.loads(p.read_text(encoding="utf-8")), indent=1)[:6000])
            except (OSError, json.JSONDecodeError):
                parts.append(f"{name}: present but unreadable")
    eng = root / "engine_state.json"
    if eng.exists():
        try:
            e = json.loads(eng.read_text(encoding="utf-8"))
            parts.append("ENGINE: " + json.dumps({k: e.get(k) for k in ("ok", "applied", "positions")}, default=str)[:800])
        except (OSError, json.JSONDecodeError):
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
    "plain English, for an operator who will read one paragraph. Name any contradiction between "
    "the artifacts. Quote figures exactly as given; never compute or estimate one. You do not "
    "recommend trades and you never state that a candidate should be executed. "
    "Reply ONLY with JSON: {\"agent\":\"chief\",\"summary\":str,\"contradictions\":[str],"
    "\"watch\":str}."
)


def scout(headlines: list[dict], facts: str, key: str, root: Path | None = None, **kw) -> dict:
    payload = "SNAPSHOT:\n" + facts + "\n\nHEADLINES:\n" + json.dumps(headlines[:40], indent=1)
    note = chat([{"role": "system", "content": SCOUT_SYS},
                 {"role": "user", "content": payload}], key, root=root, **kw)
    return _guard(note, facts + json.dumps(headlines))


def skeptic(results: list[dict], facts: str, key: str, root: Path | None = None, **kw) -> dict:
    if not results:
        return {"agent": "skeptic", "status": "no candidate", "verdict": "no candidate",
                "reason": "tier 3 has no results on disk"}
    top = results[0]
    note = chat([{"role": "system", "content": SKEPTIC_SYS},
                 {"role": "user", "content": "CANDIDATE:\n" + json.dumps(top, indent=1)[:6000]
                  + "\n\nCONTEXT:\n" + facts}], key, root=root, **kw)
    note.setdefault("symbol", top.get("symbol"))
    return _guard(note, facts + json.dumps(top))


def chief(facts: str, key: str, root: Path | None = None, **kw) -> dict:
    note = chat([{"role": "system", "content": CHIEF_SYS},
                 {"role": "user", "content": facts}], key, root=root, **kw)
    return _guard(note, facts)


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
    for url in feeds:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "zero-trust-trader/1.0"})
            with urllib.request.urlopen(req, timeout=20) as r:
                raw = r.read(MAX_FEED)
            if b"<!DOCTYPE" in raw[:2048] or b"<!ENTITY" in raw:
                raise ValueError("feed declares a DTD — refused")
            tree = ElementTree.fromstring(raw)
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
        notes["roles"]["chief"] = {"agent": "chief", "status": "skipped: no key"}
        return notes

    kw = {"model": model} if model else {}
    t3 = {}
    try:
        t3 = json.loads((root / "tier3_results.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pass

    if feeds:
        try:
            notes["roles"]["scout"] = scout(headlines(feeds), facts, key, root, **kw)
        except DeskError as e:
            notes["roles"]["scout"] = {"agent": "scout", "status": "error", "reason": str(e)}
    else:
        notes["roles"]["scout"] = {"agent": "scout", "status": "no feed configured",
                                   "reason": "add feeds to config.toml [agents] news_feeds"}

    for role, fn, arg in (("skeptic", skeptic, (t3.get("results") or [], facts)),
                          ("chief", chief, (facts,))):
        try:
            notes["roles"][role] = fn(*arg, key, root, **kw)
        except DeskError as e:
            notes["roles"][role] = {"agent": role, "status": "error", "reason": str(e)}

    rejected = [r for r in notes["roles"].values() if r.get("status") == "rejected"]
    notes["status"] = "rejected" if rejected else "ok"
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
        "facts_sha": notes.get("facts_sha"),
        "roles": role_state,
        "chief": (chief.get("summary") or "")[:400],
        "rejected": [v.get("reason") for v in (notes.get("roles") or {}).values()
                     if v.get("status") == "rejected"],
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
