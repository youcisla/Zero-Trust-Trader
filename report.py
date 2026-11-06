#!/usr/bin/env python3
"""Render one scan as a single readable HTML page.

Borrowed from TradingAgents' habit of saving each run as one HTML report, and
from what makes those reports useful: you can read the argument before the
money moves. Here the argument is the two gates. Each candidate either cleared
both, or it did not, and the page shows exactly which one stopped it and by how
much -- including the naive threshold it would have passed.

    python report.py                 # latest tier3_results.json
    python report.py path/to/res.json
"""

from __future__ import annotations

import html
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT_DIR = HERE / "reports"
SRC = HERE / "tier3_results.json"


def code_sha() -> str:
    """The commit that produced these numbers. Without it a report is a story."""
    try:
        return subprocess.run(
            ["git", "-C", str(HERE), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def esc(v) -> str:
    return html.escape(str(v if v is not None else "—"))


def verdict_block(doc: dict) -> tuple[str, str]:
    if doc.get("stood_down"):
        return ("STAND DOWN", "down",
                "No candidate cleared both gates. Standing down is the result, "
                "not an error: the system declined to trade its own hypothesis.")
    winners = doc.get("survivors") or []
    return ("PROPOSAL", "up",
            "Candidate(s) cleared both gates: " + ", ".join(esc(w) for w in winners) +
            ". Execution still requires an approval a human types.")


def gate_row(label: str, passed: bool, detail: str) -> str:
    cls = "ok" if passed else "no"
    mark = "passed" if passed else "failed"
    return (f'<tr><td class="g">{esc(label)}</td>'
            f'<td class="{cls}">{mark}</td>'
            f'<td class="d">{detail}</td></tr>')


def candidate_card(r: dict) -> str:
    h = r.get("history") or {}
    s = r.get("significance") or {}
    p = r.get("permutation") or {}

    gate = s.get("gate") or "z"
    z = s.get("t_stat_R") if gate == "t" else s.get("z")
    zc = s.get("z_crit_bonferroni")
    zn = s.get("z_crit_naive")
    pval = p.get("p_value")
    g1 = bool(s.get("passes_corrected"))
    # Trust the kernel's verdict: p < 0.05 alone also passes a loser that only
    # out-lost its random-entry null. permutation["passes"] requires mean R > 0.
    g2 = bool(p.get("passes"))

    g1_detail = (f'{esc(gate)} = {esc(z)} against a corrected bar of {esc(zc)} '
                 f'({esc(s.get("n_tests"))} cumulative tests). '
                 f'A naive 1.96 test would have said '
                 f'{"pass" if s.get("passes_naive") else "fail"} — '
                 f'the bar is what stops a false positive.')
    g2_detail = (f'permutation p = {esc(pval)} over '
                 f'{esc(p.get("permutations"))} random-entry runs '
                 f'(null mean z {esc(p.get("null_z_mean"))}).')

    both = g1 and g2
    head_cls = "ok" if both else "no"
    return f"""
    <article class="card">
      <header>
        <span class="sym">{esc(r.get("symbol"))}</span>
        <span class="strat">{esc(r.get("strategy"))}</span>
        <span class="tag {head_cls}">{"both gates cleared" if both else "rejected"}</span>
      </header>
      <p class="rule">{esc(r.get("rule"))}</p>
      <div class="stats">
        <div><b>{esc(h.get("n_decided"))}</b><span>trades</span></div>
        <div><b>{esc(_pct(h.get("win_rate")))}</b><span>win rate</span></div>
        <div><b>{esc(h.get("avg_R"))}</b><span>avg R</span></div>
        <div><b>{esc(h.get("max_drawdown_R"))}</b><span>max DD (R)</span></div>
      </div>
      <table class="gates">
        {gate_row("gate 1 — corrected significance", g1, g1_detail)}
        {gate_row("gate 2 — permutation null", g2, g2_detail)}
      </table>
      <p class="why"><span>verdict</span> {
        "both gates cleared; the decision passes to the consent step."
        if both else
        "one gate held. The scan was recorded and the trade was not taken."
      }</p>
    </article>"""


def _pct(v):
    return f"{v * 100:.1f}%" if isinstance(v, (int, float)) else None


CSS = """
:root{--bg:#0e1116;--fg:#e6edf3;--dim:#9aa7b4;--line:#232b36;--ok:#3fb950;
--no:#f85149;--warn:#d29922;--card:#151b23}
*{box-sizing:border-box}
body{margin:0;padding:40px 24px;background:var(--bg);color:var(--fg);
font:14px/1.55 ui-sans-serif,-apple-system,"Segoe UI",Roboto,sans-serif}
.wrap{max-width:860px;margin:0 auto}
h1{font-size:20px;font-weight:600;margin:0 0 4px}
.sub{color:var(--dim);font-size:12px;margin-bottom:24px}
.banner{padding:14px 16px;border-radius:8px;margin-bottom:24px;
border:1px solid var(--line);background:var(--card)}
.banner.down{border-color:rgba(248,81,73,.35)}
.banner.up{border-color:rgba(63,185,80,.35)}
.banner b{letter-spacing:.08em;font-size:13px}
.banner.down b{color:var(--no)} .banner.up b{color:var(--ok)}
.banner p{margin:6px 0 0;color:var(--dim);font-size:13px}
.prov{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));
gap:10px;margin-bottom:28px}
.prov div{background:var(--card);border:1px solid var(--line);border-radius:6px;
padding:8px 10px}
.prov span{display:block;color:var(--dim);font-size:10px;text-transform:uppercase;
letter-spacing:.07em}
.prov b{font:600 13px ui-monospace,Consolas,monospace}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;
padding:16px;margin-bottom:14px}
.card header{display:flex;align-items:center;gap:10px;margin-bottom:8px}
.sym{font:600 15px ui-monospace,Consolas,monospace}
.strat{color:var(--dim);font-size:12px;text-transform:uppercase;letter-spacing:.06em}
.tag{margin-left:auto;font-size:11px;padding:2px 8px;border-radius:999px;
border:1px solid var(--line)}
.tag.ok{color:var(--ok);border-color:rgba(63,185,80,.4)}
.tag.no{color:var(--no);border-color:rgba(248,81,73,.4)}
.rule{margin:0 0 12px;color:var(--dim);font-size:12.5px}
.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-bottom:12px}
.stats b{display:block;font:600 15px ui-monospace,Consolas,monospace}
.stats span{color:var(--dim);font-size:10px;text-transform:uppercase;
letter-spacing:.06em}
table.gates{width:100%;border-collapse:collapse;font-size:12.5px}
table.gates td{padding:7px 8px;border-top:1px solid var(--line);vertical-align:top}
td.g{color:var(--dim);white-space:nowrap;width:1%}
td.ok{color:var(--ok)} td.no{color:var(--no)}
td.d{color:var(--fg);font-size:12px}
.why{margin:12px 0 0;font-size:12.5px;color:var(--dim)}
.why span{color:var(--fg);text-transform:uppercase;font-size:10px;
letter-spacing:.07em;margin-right:6px}
footer{margin-top:28px;color:var(--dim);font-size:11.5px;border-top:1px solid var(--line);
padding-top:14px}
"""


def render(doc: dict, src_path: Path) -> str:
    title, cls, note = verdict_block(doc)
    cards = "".join(candidate_card(r) for r in (doc.get("results") or []))
    if not cards:
        cards = ('<p class="sub">No candidate reached the deep pass in this '
                 'scan — nothing triggered at tier 2.</p>')

    prov = f"""
  <div class="prov">
    <div><span>generated</span><b>{esc(doc.get("generated_utc"))}</b></div>
    <div><span>cumulative trials</span><b>{esc(doc.get("trials_cumulative"))}</b></div>
    <div><span>corrected bar</span><b>{esc(doc.get("z_crit_corrected"))}</b></div>
    <div><span>permutations</span><b>{esc(doc.get("permutations"))}</b></div>
    <div><span>code</span><b>{esc(code_sha())}</b></div>
    <div><span>source</span><b>{esc(src_path.name)}</b></div>
  </div>"""

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Scan report — {esc(doc.get("generated_utc"))}</title>
<style>{CSS}</style></head>
<body><div class="wrap">
  <h1>Scan report</h1>
  <p class="sub">One scan, both gates, every candidate. Generated by report.py.</p>

  <div class="banner {cls}"><b>{title}</b><p>{note}</p></div>
  {prov}
  {cards}

  <footer>
    Every number above comes from {esc(src_path.name)}, written by the engine at
    the timestamp shown. The system placed no order in this scan: execution needs
    <code>ZTT_ALLOW_APPLY=1</code> in the launching shell and a proposal that
    survived both gates. Reproduce with the same code SHA, seed and data window.
  </footer>
</div></body></html>
"""


def write_report(src: Path) -> Path:
    """Render `src` (a tier3 results JSON) to reports/run-<utc>.html."""
    doc = json.loads(Path(src).read_text(encoding="utf-8"))
    OUT_DIR.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out = OUT_DIR / f"run-{stamp}.html"
    out.write_text(render(doc, Path(src)), encoding="utf-8", newline="\n")
    return out


def main() -> int:
    import sys
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else SRC
    if not src.exists():
        print(f"no results file at {src}")
        return 1
    out = write_report(src)
    print(f"wrote {out}  ({out.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
