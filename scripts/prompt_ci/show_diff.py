#!/usr/bin/env python
"""
show_diff.py
------------
Visual diff tool for prompt changes.

Compares the stored syrupy snapshots (before) against freshly rendered prompts
(after), then writes an HTML file with side-by-side coloured diff and opens it
in the browser.

Usage (from project root):
    python scripts/prompt_ci/show_diff.py            # all test cases
    python scripts/prompt_ci/show_diff.py ae_term    # filter by name fragment
    python scripts/prompt_ci/show_diff.py --no-open  # write HTML but don't open browser
"""

from __future__ import annotations

import argparse
import difflib
import html as html_mod
import re
import sys
import webbrowser
from pathlib import Path

# ── project root on sys.path ──────────────────────────────────────────────────
_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from src.prompts.loader import clear_cache  # noqa: E402
from src.prompts.sdtm_prompts_simple import SDTMPromptGenerator  # noqa: E402

# ── paths ─────────────────────────────────────────────────────────────────────
_SNAPSHOT_PATH = _ROOT / "tests" / "unit" / "__snapshots__" / "test_prompt_snapshot.ambr"
_OUTPUT_PATH = _ROOT / "diff_output.html"

# ── test case definitions (mirror test_prompt_snapshot.py) ────────────────────
_TEST_CASES: list[dict] = [
    {
        "name": "test_simple_ae_term",
        "table": "不良事件表",
        "var_name": "AETERM",
        "annotation_variable": "不良事件名称",
        "annotation_table": "不良事件",
        "domain_hints": ["AE"],
        "language": "cn",
    },
    {
        "name": "test_supp_ae_comment",
        "table": "不良事件表",
        "var_name": "AECOM",
        "annotation_variable": "不良事件备注",
        "annotation_table": "不良事件",
        "domain_hints": ["AE"],
        "language": "cn",
    },
    {
        "name": "test_fa_domain",
        "table": "肿瘤病史表",
        "var_name": "THLOC",
        "annotation_variable": "当前疾病部位",
        "annotation_table": "肿瘤病史",
        "domain_hints": ["FA"],
        "language": "cn",
    },
    {
        "name": "test_multi_domain_date",
        "table": "不良事件表",
        "var_name": "AESTDTC",
        "annotation_variable": "不良事件开始日期",
        "annotation_table": "不良事件",
        "domain_hints": ["AE", "CM"],
        "language": "cn",
    },
    {
        "name": "test_no_domain_hints",
        "table": "受试者信息",
        "var_name": "SUBJID",
        "annotation_variable": "受试者编号",
        "annotation_table": "基本信息",
        "domain_hints": None,
        "language": "cn",
    },
    {
        "name": "test_english_language",
        "table": "Adverse Events",
        "var_name": "AETERM",
        "annotation_variable": "Adverse event term",
        "annotation_table": "AE",
        "domain_hints": ["AE"],
        "language": "en",
    },
]


# ── snapshot parser ────────────────────────────────────────────────────────────


def _parse_snapshots(path: Path) -> dict[str, str]:
    """
    Parse a syrupy .ambr file into {test_name: prompt_text}.
    Format:
        # name: TestPromptSnapshots.test_xxx
          '''
          ...text...
          '''
    """
    text = path.read_text(encoding="utf-8")
    pattern = re.compile(
        r"# name: TestPromptSnapshots\.(\w+)\n"
        r"  '''\n"
        r"(.*?)"
        r"\n  '''",
        re.DOTALL,
    )
    result: dict[str, str] = {}
    for m in pattern.finditer(text):
        name = m.group(1)
        # syrupy indents each line with 2 spaces
        body = re.sub(r"^  ", "", m.group(2), flags=re.MULTILINE)
        result[name] = body
    return result


# ── prompt renderer ────────────────────────────────────────────────────────────


def _render(tc: dict) -> str:
    clear_cache()
    g = SDTMPromptGenerator(language=tc["language"])
    variable_data = {
        "metadata_variable": tc["var_name"],
        "annotation_variable": tc["annotation_variable"],
        "annotation_table": tc["annotation_table"],
        "type": "text",
    }
    return g.generate_variable_prompt(
        table_name=tc["table"],
        variable_name=tc["var_name"],
        variable_data=variable_data,
        all_table_variables=[{"metadata_variable": tc["var_name"], "annotation_variable": tc["annotation_variable"]}],
        domain_hints=tc["domain_hints"],
    )


# ── HTML diff builder ──────────────────────────────────────────────────────────

_CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: 'Segoe UI', system-ui, sans-serif;
  font-size: 13px;
  background: #f5f5f5;
  color: #222;
}
.page { max-width: 1600px; margin: 0 auto; padding: 24px; }
h1 { font-size: 18px; font-weight: 600; margin-bottom: 4px; }
.subtitle { color: #666; font-size: 12px; margin-bottom: 24px; }
.case { background: #fff; border: 1px solid #ddd; border-radius: 6px;
        margin-bottom: 20px; overflow: hidden; }
.case-header {
  display: flex; align-items: center; gap: 12px;
  padding: 10px 16px; background: #fafafa;
  border-bottom: 1px solid #e0e0e0;
  cursor: pointer; user-select: none;
}
.case-header:hover { background: #f0f0f0; }
.case-name { font-weight: 600; font-size: 13px; }
.badge {
  font-size: 11px; font-weight: 600; padding: 2px 8px;
  border-radius: 3px; letter-spacing: 0.4px;
}
.badge-changed  { background: #fff3cd; color: #856404; }
.badge-same     { background: #d1e7dd; color: #0f5132; }
.badge-no-snap  { background: #f8d7da; color: #842029; }
.toggle { margin-left: auto; font-size: 11px; color: #888; }
.case-body { display: none; }
.case-body.open { display: block; }
.diff-table { width: 100%; border-collapse: collapse; font-family: monospace;
              font-size: 12px; line-height: 1.5; }
.diff-table td { vertical-align: top; padding: 2px 10px; white-space: pre-wrap;
                 word-break: break-word; }
.col-before { width: 50%; border-right: 1px solid #e0e0e0; }
.col-after  { width: 50%; }
.diff-header td { background: #f0f0f0; font-weight: 600; font-size: 11px;
                  text-transform: uppercase; letter-spacing: 0.5px;
                  padding: 6px 10px; border-bottom: 1px solid #e0e0e0; }
.del  { background: #ffeef0; }
.ins  { background: #e6ffed; }
.ctx  { background: #fff; color: #444; }
.del .mark { background: #fdb8c0; }
.ins .mark { background: #acf2bd; }
.same-msg { padding: 16px; color: #666; font-size: 12px; }
"""

_JS = """
function toggle(id) {
  const body = document.getElementById(id);
  const btn = body.previousElementSibling.querySelector('.toggle');
  if (body.classList.toggle('open')) { btn.textContent = 'collapse'; }
  else { btn.textContent = 'expand'; }
}
"""


def _inline_diff_rows(before: str, after: str) -> str:
    """Generate inline diff <tr> rows for the two texts."""
    before_lines = before.splitlines()
    after_lines = after.splitlines()

    sm = difflib.SequenceMatcher(None, before_lines, after_lines, autojunk=False)
    rows: list[str] = []

    def _esc(s: str) -> str:
        return html_mod.escape(s)

    def _char_diff(a: str, b: str) -> tuple[str, str]:
        """Return HTML for character-level highlighted a and b."""
        csm = difflib.SequenceMatcher(None, a, b, autojunk=False)
        out_a, out_b = [], []
        for op, i1, i2, j1, j2 in csm.get_opcodes():
            if op == "equal":
                out_a.append(_esc(a[i1:i2]))
                out_b.append(_esc(b[j1:j2]))
            elif op == "delete":
                out_a.append(f'<span class="mark">{_esc(a[i1:i2])}</span>')
            elif op == "insert":
                out_b.append(f'<span class="mark">{_esc(b[j1:j2])}</span>')
            elif op == "replace":
                out_a.append(f'<span class="mark">{_esc(a[i1:i2])}</span>')
                out_b.append(f'<span class="mark">{_esc(b[j1:j2])}</span>')
        return "".join(out_a), "".join(out_b)

    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            for line in before_lines[i1:i2]:
                rows.append(
                    f'<tr class="ctx">'
                    f'<td class="col-before">{_esc(line)}</td>'
                    f'<td class="col-after">{_esc(line)}</td></tr>'
                )
        elif op == "delete":
            for line in before_lines[i1:i2]:
                rows.append(f'<tr class="del"><td class="col-before">{_esc(line)}</td><td class="col-after"></td></tr>')
        elif op == "insert":
            for line in after_lines[j1:j2]:
                rows.append(f'<tr class="ins"><td class="col-before"></td><td class="col-after">{_esc(line)}</td></tr>')
        elif op == "replace":
            # Pair up lines for character-level diff where counts match
            b_seg = before_lines[i1:i2]
            a_seg = after_lines[j1:j2]
            for idx in range(max(len(b_seg), len(a_seg))):
                bl = b_seg[idx] if idx < len(b_seg) else ""
                al = a_seg[idx] if idx < len(a_seg) else ""
                if bl and al:
                    hl_b, hl_a = _char_diff(bl, al)
                    rows.append(f'<tr class="del"><td class="col-before">{hl_b}</td><td class="col-after"></td></tr>')
                    rows.append(f'<tr class="ins"><td class="col-before"></td><td class="col-after">{hl_a}</td></tr>')
                elif bl:
                    rows.append(
                        f'<tr class="del"><td class="col-before">{_esc(bl)}</td><td class="col-after"></td></tr>'
                    )
                else:
                    rows.append(
                        f'<tr class="ins"><td class="col-before"></td><td class="col-after">{_esc(al)}</td></tr>'
                    )

    return "\n".join(rows)


def _build_html(cases: list[dict[str, str]]) -> str:
    changed_count = sum(1 for c in cases if c["status"] == "changed")
    same_count = sum(1 for c in cases if c["status"] == "same")
    no_snap_count = sum(1 for c in cases if c["status"] == "no_snapshot")

    summary = (
        f"<span class='badge badge-changed'>{changed_count} changed</span> "
        f"<span class='badge badge-same'>{same_count} same</span>"
        + (f" <span class='badge badge-no-snap'>{no_snap_count} no snapshot</span>" if no_snap_count else "")
    )

    blocks: list[str] = []
    for i, c in enumerate(cases):
        badge_cls = {"changed": "badge-changed", "same": "badge-same", "no_snapshot": "badge-no-snap"}.get(
            c["status"], ""
        )
        badge_label = {"changed": "CHANGED", "same": "SAME", "no_snapshot": "NO SNAPSHOT"}.get(c["status"], "")

        auto_open = "open" if c["status"] == "changed" else ""

        if c["status"] == "same":
            body_inner = "<div class='same-msg'>No changes — rendered output matches snapshot.</div>"
        elif c["status"] == "no_snapshot":
            body_inner = (
                "<div class='same-msg'>No stored snapshot yet. Run pytest --snapshot-update to create one.</div>"
            )
        else:
            rows = _inline_diff_rows(c["before"], c["after"])
            body_inner = f"""
            <table class="diff-table">
              <tr class="diff-header">
                <td class="col-before">Before (snapshot)</td>
                <td class="col-after">After (current render)</td>
              </tr>
              {rows}
            </table>"""

        blocks.append(f"""
        <div class="case">
          <div class="case-header" onclick="toggle('body-{i}')">
            <span class="case-name">{html_mod.escape(c["name"])}</span>
            <span class="badge {badge_cls}">{badge_label}</span>
            <span class="toggle">{"expand" if not auto_open else "collapse"}</span>
          </div>
          <div class="case-body {auto_open}" id="body-{i}">{body_inner}</div>
        </div>""")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Prompt Diff</title>
<style>{_CSS}</style>
</head>
<body>
<div class="page">
  <h1>Prompt Diff</h1>
  <p class="subtitle">{summary} &nbsp;·&nbsp; Snapshot: {_SNAPSHOT_PATH.name}</p>
  {"".join(blocks)}
</div>
<script>{_JS}</script>
</body>
</html>"""


# ── main ──────────────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(description="Visual prompt diff tool")
    parser.add_argument("filter", nargs="?", default="", help="Filter test cases by name fragment")
    parser.add_argument("--no-open", action="store_true", help="Write HTML but don't open browser")
    parser.add_argument("--output", default=str(_OUTPUT_PATH), help=f"Output HTML path (default: {_OUTPUT_PATH})")
    args = parser.parse_args()

    # Load snapshots
    snapshots: dict[str, str] = {}
    if _SNAPSHOT_PATH.exists():
        snapshots = _parse_snapshots(_SNAPSHOT_PATH)

    # Select test cases
    test_cases = [tc for tc in _TEST_CASES if not args.filter or args.filter.lower() in tc["name"].lower()]

    if not test_cases:
        print(f"No test cases match filter: {args.filter!r}")
        return 1

    print(f"Comparing {len(test_cases)} test case(s)...")
    case_results: list[dict[str, str]] = []

    for tc in test_cases:
        name = tc["name"]
        print(f"  Rendering: {name} ...", end=" ", flush=True)
        current = _render(tc)
        stored = snapshots.get(name)

        if stored is None:
            status = "no_snapshot"
            before = after = ""
            print("no snapshot")
        elif stored.strip() == current.strip():
            status = "same"
            before = after = current
            print("same")
        else:
            status = "changed"
            before = stored
            after = current
            print("CHANGED")

        case_results.append({"name": name, "status": status, "before": before, "after": after})

    html_content = _build_html(case_results)
    out = Path(args.output)
    out.write_text(html_content, encoding="utf-8")
    print(f"\nDiff report written to: {out}")

    if not args.no_open:
        webbrowser.open(out.as_uri())

    changed = sum(1 for c in case_results if c["status"] == "changed")
    return 1 if changed else 0


if __name__ == "__main__":
    sys.exit(main())
