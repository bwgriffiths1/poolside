"""Build a self-contained side-by-side HTML viewer for two eval runs.

    python -m tools.eval.viewer --a post-batch1 --b pre-batch1 \
        [--out compare.html] [--blind] [--title "…"]

--a/--b accept run ids under tools/eval/runs/ or paths to run directories
(e.g. an archived copy). The output is ONE html file, no external requests:
case picker, matched-entity picker, judge scores + defects, pairwise
verdicts, and the two summaries rendered side by side in reading serif.

--blind randomizes which run appears on which side per case, hides run
labels, models, and all judge/pairwise metadata (that is the point of a
blind read), and writes <out>.key.json — read it only after ranking.
"""
from __future__ import annotations

import argparse
import html
import json
import random
import re
import sys
from pathlib import Path

from tools.eval.cases import CASES

EVAL_DIR = Path(__file__).resolve().parent
RUNS_DIR = EVAL_DIR / "runs"

_PRIMARY = {"iso_item": "agenda_item", "iso_meeting": "meeting",
            "ferc_filing": "docket_filing"}


# ---------------------------------------------------------------------------
# Minimal markdown → HTML (headings, bullets, tables, bold/italic/code,
# callouts, figure refs). Deliberately small: summaries are well-formed
# pipeline output, not arbitrary markdown.
# ---------------------------------------------------------------------------

def _inline(s: str) -> str:
    s = html.escape(s, quote=False)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"(?<!\w)\*([^*\n]+)\*(?!\w)", r"<em>\1</em>", s)
    s = s.replace("\\$", "$")
    return s


def md_to_html(md: str) -> str:
    out: list[str] = []
    lines = (md or "").splitlines()
    i, n = 0, len(lines)
    in_list = False

    def close_list():
        nonlocal in_list
        if in_list:
            out.append("</ul>")
            in_list = False

    while i < n:
        line = lines[i]
        stripped = line.strip()
        img = re.match(r"<!-- image_id:(\d+) -->", stripped)
        if not stripped:
            close_list()
            i += 1
            continue
        if img:
            out.append(f'<div class="figref">figure · image #{img.group(1)} '
                       f'(bytes live in the app, not this file)</div>')
            i += 1
            continue
        if stripped.startswith("|") and i + 1 < n and set(lines[i + 1].strip()) <= set("|-: "):
            close_list()
            rows = []
            while i < n and lines[i].strip().startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                rows.append(cells)
                i += 1
            out.append('<div class="tablewrap"><table>')
            for r_i, cells in enumerate(rows):
                if r_i == 1:
                    continue  # separator row
                tag = "th" if r_i == 0 else "td"
                out.append("<tr>" + "".join(
                    f"<{tag}>{_inline(c)}</{tag}>" for c in cells) + "</tr>")
            out.append("</table></div>")
            continue
        m = re.match(r"(#{1,6})\s+(.*)", stripped)
        if m:
            close_list()
            lvl = min(len(m.group(1)) + 1, 6)  # demote: pane header is h2
            out.append(f"<h{lvl}>{_inline(m.group(2))}</h{lvl}>")
            i += 1
            continue
        if stripped in ("---", "***"):
            close_list()
            out.append("<hr>")
            i += 1
            continue
        cm = re.match(r">\s*\[!(\w+)\]\s*(.*)", stripped)
        if cm:
            close_list()
            out.append(f'<div class="callout"><span class="callout-label">'
                       f'{html.escape(cm.group(1))}</span> {_inline(cm.group(2))}</div>')
            i += 1
            continue
        if stripped.startswith("> "):
            close_list()
            out.append(f"<blockquote>{_inline(stripped[2:])}</blockquote>")
            i += 1
            continue
        if re.match(r"[-*]\s+", stripped):
            if not in_list:
                out.append("<ul>")
                in_list = True
            item = [re.sub(r"^[-*]\s+", "", stripped)]
            i += 1
            while i < n and lines[i].startswith("  ") and lines[i].strip() \
                    and not re.match(r"\s*[-*]\s+", lines[i]):
                item.append(lines[i].strip())
                i += 1
            out.append(f"<li>{_inline(' '.join(item))}</li>")
            continue
        para = [stripped]
        i += 1
        while i < n and lines[i].strip() and not re.match(
                r"(#{1,6}\s|[-*]\s|\||>|---$|<!-- image_id)", lines[i].strip()):
            para.append(lines[i].strip())
            i += 1
        close_list()
        out.append(f"<p>{_inline(' '.join(para))}</p>")
    close_list()
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Data assembly
# ---------------------------------------------------------------------------

def _resolve_run(arg: str) -> Path:
    p = Path(arg).expanduser()
    if p.is_dir():
        return p
    p = RUNS_DIR / arg
    if p.is_dir():
        return p
    sys.exit(f"run not found: {arg} (not a directory, not under {RUNS_DIR})")


def _load_run(run_dir: Path) -> dict:
    recs = {}
    for p in sorted(run_dir.glob("*.json")):
        if p.stem in CASES:
            recs[p.stem] = json.loads(p.read_text(encoding="utf-8"))
    judge = {}
    for name in ("scores_judge.json", "scores.json"):
        f = run_dir / name
        if f.exists():
            judge = json.loads(f.read_text(encoding="utf-8")).get("judge", {})
            if judge:
                break
    pairwise = {}
    for name in ("scores_pairwise.json", "scores.json"):
        f = run_dir / name
        if f.exists():
            pairwise = json.loads(f.read_text(encoding="utf-8")).get("pairwise", {})
            if pairwise:
                break
    return {"dir": run_dir, "recs": recs, "judge": judge, "pairwise": pairwise}


def _entity_key(o: dict) -> str:
    return f"{o['entity_type']}:{o['entity_id']}"


def _unescape(x):
    """clean_output escapes $ as \\$ in LLM text — undo for display."""
    if isinstance(x, str):
        return x.replace("\\$", "$")
    if isinstance(x, list):
        return [_unescape(v) for v in x]
    if isinstance(x, dict):
        return {k: _unescape(v) for k, v in x.items()}
    return x


def _pane(o: dict, judge: dict | None, blind: bool) -> dict:
    return {
        "model": None if blind else o.get("model_id"),
        "one_line": _unescape(o.get("one_line")),
        "html": md_to_html(o.get("detailed") or ""),
        "words": len((o.get("detailed") or "").split()),
        "judge": None if blind else _unescape(judge),
    }


def build(a: dict, b: dict, blind: bool, title: str) -> tuple[str, dict]:
    label_a = "Side 1" if blind else a["dir"].name
    label_b = "Side 2" if blind else b["dir"].name
    key = {"blind": blind, "assignment": {}}
    cases = []
    for case_id in sorted(set(a["recs"]) & set(b["recs"])):
        ra, rb = a["recs"][case_id], b["recs"][case_id]
        swap = blind and random.random() < 0.5
        left_run, right_run = (b, a) if swap else (a, b)
        left_rec, right_rec = (rb, ra) if swap else (ra, rb)
        key["assignment"][case_id] = {
            "left": left_run["dir"].name, "right": right_run["dir"].name}

        left_by = {_entity_key(o): o for o in left_rec["outputs"]}
        right_by = {_entity_key(o): o for o in right_rec["outputs"]}
        shared = [k for k in left_by if k in right_by]
        primary_type = _PRIMARY.get(ra["kind"])
        shared.sort(key=lambda k: (not k.startswith(primary_type + ":"), k))
        if not shared:
            continue
        entities = {}
        for k in shared:
            is_primary = k == shared[0]
            entities[k] = {
                "left": _pane(left_by[k],
                              left_run["judge"].get(case_id) if is_primary else None,
                              blind),
                "right": _pane(right_by[k],
                               right_run["judge"].get(case_id) if is_primary else None,
                               blind),
            }
        pw = None
        if not blind:
            pw = _unescape(a["pairwise"].get(case_id) or b["pairwise"].get(case_id))
        cases.append({
            "id": case_id,
            "desc": CASES.get(case_id, {}).get("description", ""),
            "kind": ra["kind"],
            "entities": entities,
            "order": shared,
            "pairwise": pw,
        })

    data = {"title": title, "labelA": label_a, "labelB": label_b,
            "cases": cases}
    return _render_page(data), key


# ---------------------------------------------------------------------------
# Page template
# ---------------------------------------------------------------------------

_SHELL_OPEN = ("<!DOCTYPE html>\n<html lang=\"en\">\n<head>\n"
               "<meta charset=\"utf-8\">\n"
               "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
               "<title>{title}</title>\n</head>\n<body>\n")
_SHELL_CLOSE = "\n</body>\n</html>\n"

_STYLE = """
<style>
:root {
  --bg: #FAFAF7; --surface: #FFFFFF; --ink: #24303A; --muted: #66727C;
  --accent: #2F6B8A; --line: #E4E2DA; --chipbg: #EFF3F5;
  --good: #2E7D5B; --warn: #B45309; --bad: #B3372F;
  --serif: "Charter", "Iowan Old Style", Georgia, "Times New Roman", serif;
  --ui: -apple-system, "Segoe UI", system-ui, Helvetica, Arial, sans-serif;
  --mono: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
}
@media (prefers-color-scheme: dark) { :root {
  --bg: #14181C; --surface: #1C2228; --ink: #E2E6EA; --muted: #93A1AC;
  --accent: #74AECB; --line: #2A323B; --chipbg: #232C34;
  --good: #4CAF87; --warn: #D9862F; --bad: #D96459; } }
:root[data-theme="dark"] {
  --bg: #14181C; --surface: #1C2228; --ink: #E2E6EA; --muted: #93A1AC;
  --accent: #74AECB; --line: #2A323B; --chipbg: #232C34;
  --good: #4CAF87; --warn: #D9862F; --bad: #D96459; }
:root[data-theme="light"] {
  --bg: #FAFAF7; --surface: #FFFFFF; --ink: #24303A; --muted: #66727C;
  --accent: #2F6B8A; --line: #E4E2DA; --chipbg: #EFF3F5;
  --good: #2E7D5B; --warn: #B45309; --bad: #B3372F; }
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--ink);
  font-family: var(--ui); font-size: 15px; line-height: 1.55; }
header { position: sticky; top: 0; z-index: 5; background: var(--bg);
  border-bottom: 1px solid var(--line); padding: 10px 20px;
  display: flex; flex-wrap: wrap; align-items: center; gap: 10px 14px; }
header h1 { font-size: 15px; margin: 0 8px 0 0; font-weight: 650; }
header .sub { color: var(--muted); font-size: 12.5px; width: 100%; }
select, button { font: inherit; color: var(--ink); background: var(--surface);
  border: 1px solid var(--line); border-radius: 7px; padding: 5px 9px; }
button { cursor: pointer; } button:hover { border-color: var(--accent); }
select:focus-visible, button:focus-visible {
  outline: 2px solid var(--accent); outline-offset: 1px; }
.verdict { font-size: 12.5px; color: var(--muted); }
.verdict b { color: var(--ink); }
main { display: grid; grid-template-columns: 1fr 1fr; gap: 16px;
  padding: 16px 20px 60px; max-width: 1700px; margin: 0 auto; }
@media (max-width: 900px) { main { grid-template-columns: 1fr; } }
.pane { background: var(--surface); border: 1px solid var(--line);
  border-radius: 10px; min-width: 0; }
.pane-head { padding: 12px 16px; border-bottom: 1px solid var(--line);
  display: flex; flex-wrap: wrap; gap: 6px 10px; align-items: baseline; }
.pane-head .run { font-weight: 650; color: var(--accent);
  letter-spacing: .02em; }
.chip { font-family: var(--mono); font-size: 11px; background: var(--chipbg);
  border-radius: 5px; padding: 2px 7px; color: var(--muted);
  font-variant-numeric: tabular-nums; }
.scorechips { display: flex; flex-wrap: wrap; gap: 5px; padding: 9px 16px 0; }
.score { font-family: var(--mono); font-size: 11px; padding: 2px 7px;
  border-radius: 5px; color: #fff; font-variant-numeric: tabular-nums; }
.s-good { background: var(--good); } .s-warn { background: var(--warn); }
.s-bad { background: var(--bad); }
.tldr { margin: 12px 16px 0; padding: 10px 13px; border-left: 3px solid var(--accent);
  background: var(--chipbg); border-radius: 0 7px 7px 0;
  font-family: var(--serif); font-size: 15.5px; }
.tldr .lbl { font-family: var(--ui); font-size: 10.5px; text-transform: uppercase;
  letter-spacing: .09em; color: var(--accent); display: block; margin-bottom: 3px; }
.prose { padding: 6px 18px 18px; font-family: var(--serif); font-size: 15.5px;
  line-height: 1.62; }
.prose h2, .prose h3, .prose h4 { font-family: var(--ui); line-height: 1.25;
  margin: 1.5em 0 .45em; text-wrap: balance; }
.prose h2 { font-size: 17px; } .prose h3 { font-size: 15px; }
.prose h4 { font-size: 13.5px; color: var(--muted); text-transform: uppercase;
  letter-spacing: .05em; }
.prose hr { border: 0; border-top: 1px solid var(--line); margin: 1.4em 0; }
.prose code { font-family: var(--mono); font-size: .86em;
  background: var(--chipbg); padding: 1px 4px; border-radius: 4px; }
.prose blockquote { margin: .8em 0; padding-left: 12px;
  border-left: 3px solid var(--line); color: var(--muted); }
.callout { margin: .9em 0; padding: 9px 12px; border: 1px solid var(--line);
  border-left: 3px solid var(--warn); border-radius: 0 7px 7px 0; }
.callout-label { font-family: var(--ui); font-size: 11px; font-weight: 650;
  text-transform: uppercase; letter-spacing: .07em; color: var(--warn);
  margin-right: 6px; }
.figref { margin: .9em 0; padding: 8px 12px; border: 1px dashed var(--line);
  border-radius: 7px; color: var(--muted); font-family: var(--ui);
  font-size: 12.5px; }
.tablewrap { overflow-x: auto; margin: .9em 0; }
.prose table { border-collapse: collapse; font-family: var(--ui);
  font-size: 13px; }
.prose th, .prose td { border: 1px solid var(--line); padding: 5px 9px;
  text-align: left; vertical-align: top; }
.prose th { background: var(--chipbg); }
details.defects { margin: 10px 16px 0; font-size: 13px; }
details.defects summary { cursor: pointer; color: var(--muted); }
details.defects li { margin: 5px 0; }
.pwstrip { grid-column: 1 / -1; background: var(--surface);
  border: 1px solid var(--line); border-radius: 10px; padding: 10px 16px;
  font-size: 13px; }
.pwstrip .win { font-weight: 650; color: var(--accent); }
.pwstrip .rat { color: var(--muted); display: block; margin-top: 3px; }
@media (prefers-reduced-motion: no-preference) {
  .pane { transition: border-color .15s; } }
</style>
"""

_SCRIPT = """
<script>
const DATA = __DATA__;
const caseSel = document.getElementById('caseSel');
const entSel = document.getElementById('entSel');
const state = { c: 0, e: null };
DATA.cases.forEach((c, i) => {
  const o = document.createElement('option');
  o.value = i; o.textContent = c.id;
  caseSel.appendChild(o);
});
function scoreChips(j) {
  if (!j || j.error || j.overall === undefined) return '';
  const dims = [['overall','overall'],['faithfulness','faith'],
    ['completeness','compl'],['context_sufficiency','ctx'],
    ['actionability','act'],['format','fmt']];
  return '<div class="scorechips">' + dims.map(([k, lbl]) => {
    const v = j[k];
    const cls = v >= 4 ? 's-good' : v === 3 ? 's-warn' : 's-bad';
    return `<span class="score ${cls}">${lbl} ${v}</span>`;
  }).join('') + '</div>';
}
function defects(j) {
  if (!j || !j.defects || !j.defects.length) return '';
  return '<details class="defects"><summary>judge defects (' + j.defects.length +
    ')</summary><ul>' + j.defects.map(d =>
    `<li>${d.replace(/&/g,'&amp;').replace(/</g,'&lt;')}</li>`).join('') +
    '</ul></details>';
}
function pane(el, label, p) {
  el.querySelector('.pane-head').innerHTML =
    `<span class="run">${label}</span>` +
    (p.model ? `<span class="chip">${p.model}</span>` : '') +
    `<span class="chip">${p.words.toLocaleString()} words</span>`;
  el.querySelector('.meta').innerHTML = scoreChips(p.judge) + defects(p.judge);
  el.querySelector('.tldr').innerHTML = p.one_line
    ? `<span class="lbl">TLDR</span>${p.one_line}` : '';
  el.querySelector('.tldr').style.display = p.one_line ? '' : 'none';
  el.querySelector('.prose').innerHTML = p.html;
}
function pwHtml(pw) {
  if (!pw || pw.error) return '';
  const name = w => w === 'a' ? DATA.labelA : w === 'b' ? DATA.labelB : w;
  let out = `Pairwise verdict: <span class="win">${name(pw.verdict)}</span>` +
    (pw.consistent ? ' (order-consistent)' : '');
  (pw.orders || []).forEach((o, i) => {
    if (o.rationale) out += `<span class="rat">order ${i + 1} → ` +
      `${name(o.winner)}${o.margin ? ' (' + o.margin + ')' : ''}: ${o.rationale}</span>`;
  });
  return out;
}
function render() {
  const c = DATA.cases[state.c];
  document.getElementById('desc').textContent = c.desc;
  entSel.innerHTML = '';
  c.order.forEach(k => {
    const o = document.createElement('option');
    o.value = k; o.textContent = k;
    entSel.appendChild(o);
  });
  if (!c.entities[state.e]) state.e = c.order[0];
  entSel.value = state.e;
  entSel.style.display = c.order.length > 1 ? '' : 'none';
  const ent = c.entities[state.e];
  pane(document.getElementById('paneL'), DATA.labelA, ent.left);
  pane(document.getElementById('paneR'), DATA.labelB, ent.right);
  const pw = document.getElementById('pw');
  const showPw = state.e === c.order[0] ? pwHtml(c.pairwise) : '';
  pw.innerHTML = showPw; pw.style.display = showPw ? '' : 'none';
  caseSel.value = state.c;
}
caseSel.addEventListener('change', () => { state.c = +caseSel.value; state.e = null; render(); });
entSel.addEventListener('change', () => { state.e = entSel.value; render(); });
document.getElementById('prev').addEventListener('click', () => {
  state.c = (state.c - 1 + DATA.cases.length) % DATA.cases.length; state.e = null; render(); });
document.getElementById('next').addEventListener('click', () => {
  state.c = (state.c + 1) % DATA.cases.length; state.e = null; render(); });
document.addEventListener('keydown', e => {
  if (e.target.tagName === 'SELECT') return;
  if (e.key === 'ArrowLeft') document.getElementById('prev').click();
  if (e.key === 'ArrowRight') document.getElementById('next').click();
});
render();
</script>
"""


def _render_page(data: dict) -> str:
    payload = json.dumps(data).replace("</", "<\\/")
    body = f"""{_STYLE}
<header>
  <h1>{html.escape(data['title'])}</h1>
  <button id="prev" aria-label="previous case">&#8592;</button>
  <select id="caseSel" aria-label="case"></select>
  <button id="next" aria-label="next case">&#8594;</button>
  <select id="entSel" aria-label="entity"></select>
  <span class="verdict"><b>{html.escape(data['labelA'])}</b> (left) vs <b>{html.escape(data['labelB'])}</b> (right) · &#8592;/&#8594; keys switch cases</span>
  <span class="sub" id="desc"></span>
</header>
<main>
  <div class="pwstrip" id="pw" style="display:none"></div>
  <section class="pane" id="paneL" aria-label="left summary">
    <div class="pane-head"></div><div class="meta"></div>
    <div class="tldr"></div><div class="prose"></div>
  </section>
  <section class="pane" id="paneR" aria-label="right summary">
    <div class="pane-head"></div><div class="meta"></div>
    <div class="tldr"></div><div class="prose"></div>
  </section>
</main>
{_SCRIPT.replace("__DATA__", payload)}"""
    return body


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--a", required=True, help="run id or run-directory path")
    ap.add_argument("--b", required=True, help="run id or run-directory path")
    ap.add_argument("--out", default="compare.html")
    ap.add_argument("--title", default=None)
    ap.add_argument("--blind", action="store_true")
    ap.add_argument("--fragment", action="store_true",
                    help="emit page content without the <html> shell")
    args = ap.parse_args()

    a, b = _load_run(_resolve_run(args.a)), _load_run(_resolve_run(args.b))
    title = args.title or (
        "Blind summary comparison" if args.blind
        else f"{a['dir'].name} vs {b['dir'].name}")
    content, key = build(a, b, args.blind, title)

    out = Path(args.out).expanduser()
    page = content if args.fragment else (
        _SHELL_OPEN.format(title=html.escape(title)) + content + _SHELL_CLOSE)
    out.write_text(page, encoding="utf-8")
    print(f"wrote {out} ({out.stat().st_size // 1024} KB, "
          f"{len(key['assignment'])} case(s))")
    if args.blind:
        key_path = out.with_suffix(out.suffix + ".key.json")
        key_path.write_text(json.dumps(key, indent=2), encoding="utf-8")
        print(f"blind key (read AFTER ranking): {key_path}")


if __name__ == "__main__":
    main()
