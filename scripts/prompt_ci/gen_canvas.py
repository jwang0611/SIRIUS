#!/usr/bin/env python
"""
gen_canvas.py
-------------
Generates a Cursor Canvas (.canvas.tsx) that shows prompt diffs using the
SDK's native DiffView component.

Run from the project root:
    python scripts/prompt_ci/gen_canvas.py

The canvas is written to the Cursor-managed canvases/ directory and auto-
refreshes in the IDE.  The script also writes diff_output.html as a backup
for terminal-only environments.
"""

from __future__ import annotations

import difflib
import json
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from src.prompts.loader import clear_cache  # noqa: E402
from src.prompts.sdtm_prompts_simple import SDTMPromptGenerator  # noqa: E402

# ── paths ─────────────────────────────────────────────────────────────────────
_SNAPSHOT_PATH = _ROOT / "tests" / "unit" / "__snapshots__" / "test_prompt_snapshot.ambr"
_CANVAS_PATH = Path(
    r"C:\Users\xia2.gao\.cursor\projects\e-01-projects-isdtaim-isdtaim\canvases"
    r"\prompt-diff.canvas.tsx"
)

# ── test case definitions ─────────────────────────────────────────────────────
_TEST_CASES: list[dict] = [
    {
        "name": "simple_ae_term",
        "label": "AE term (simple standard)",
        "table": "不良事件表",
        "var_name": "AETERM",
        "annotation_variable": "不良事件名称",
        "annotation_table": "不良事件",
        "domain_hints": ["AE"],
        "language": "cn",
    },
    {
        "name": "supp_ae_comment",
        "label": "AE comment (SUPP pattern)",
        "table": "不良事件表",
        "var_name": "AECOM",
        "annotation_variable": "不良事件备注",
        "annotation_table": "不良事件",
        "domain_hints": ["AE"],
        "language": "cn",
    },
    {
        "name": "fa_domain",
        "label": "FA domain (FATESTCD)",
        "table": "肿瘤病史表",
        "var_name": "THLOC",
        "annotation_variable": "当前疾病部位",
        "annotation_table": "肿瘤病史",
        "domain_hints": ["FA"],
        "language": "cn",
    },
    {
        "name": "multi_domain_date",
        "label": "Multi-domain AE+CM (date)",
        "table": "不良事件表",
        "var_name": "AESTDTC",
        "annotation_variable": "不良事件开始日期",
        "annotation_table": "不良事件",
        "domain_hints": ["AE", "CM"],
        "language": "cn",
    },
    {
        "name": "no_domain_hints",
        "label": "No domain hints (fallback rules)",
        "table": "受试者信息",
        "var_name": "SUBJID",
        "annotation_variable": "受试者编号",
        "annotation_table": "基本信息",
        "domain_hints": None,
        "language": "cn",
    },
    {
        "name": "english_language",
        "label": "English language instruction",
        "table": "Adverse Events",
        "var_name": "AETERM",
        "annotation_variable": "Adverse event term",
        "annotation_table": "AE",
        "domain_hints": ["AE"],
        "language": "en",
    },
]


def _parse_snapshots(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    pattern = re.compile(
        r"# name: TestPromptSnapshots\.test_(\w+)\n"
        r"  '''\n(.*?)\n  '''",
        re.DOTALL,
    )
    return {m.group(1): re.sub(r"^  ", "", m.group(2), flags=re.MULTILINE) for m in pattern.finditer(text)}


def _render(tc: dict) -> str:
    clear_cache()
    g = SDTMPromptGenerator(language=tc["language"])
    return g.generate_variable_prompt(
        table_name=tc["table"],
        variable_name=tc["var_name"],
        variable_data={
            "metadata_variable": tc["var_name"],
            "annotation_variable": tc["annotation_variable"],
            "annotation_table": tc["annotation_table"],
            "type": "text",
        },
        all_table_variables=[
            {
                "metadata_variable": tc["var_name"],
                "annotation_variable": tc["annotation_variable"],
            }
        ],
        domain_hints=tc["domain_hints"],
    )


def _compute_diff_lines(before: str, after: str) -> list[dict]:
    """Return a list of DiffLineData-compatible dicts."""
    b = before.splitlines()
    a = after.splitlines()
    result: list[dict] = []
    line_num = 0
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, b, a, autojunk=False).get_opcodes():
        if op == "equal":
            for line in b[i1:i2]:
                line_num += 1
                result.append({"type": "unchanged", "content": line, "lineNumber": line_num})
        elif op in ("replace", "delete"):
            for line in b[i1:i2]:
                line_num += 1
                result.append({"type": "removed", "content": line, "lineNumber": line_num})
        if op in ("replace", "insert"):
            for line in a[j1:j2]:
                line_num += 1
                result.append({"type": "added", "content": line, "lineNumber": line_num})
    return result


def _build_case_data() -> list[dict]:
    snapshots = _parse_snapshots(_SNAPSHOT_PATH) if _SNAPSHOT_PATH.exists() else {}
    cases = []
    for tc in _TEST_CASES:
        current = _render(tc)
        stored = snapshots.get(tc["name"])
        if stored is None:
            status = "no_snapshot"
            diff_lines: list[dict] = []
            additions = deletions = 0
        elif stored.strip() == current.strip():
            status = "same"
            diff_lines = _compute_diff_lines(current, current)
            additions = deletions = 0
        else:
            status = "changed"
            diff_lines = _compute_diff_lines(stored, current)
            additions = sum(1 for line in diff_lines if line["type"] == "added")
            deletions = sum(1 for line in diff_lines if line["type"] == "removed")

        print(f"  {tc['name']}: {status}" + (f"  (+{additions} -{deletions})" if status == "changed" else ""))
        cases.append(
            {
                "name": tc["name"],
                "label": tc["label"],
                "status": status,
                "additions": additions if status == "changed" else 0,
                "deletions": deletions if status == "changed" else 0,
                "diffLines": diff_lines,
            }
        )
    return cases


def _write_canvas(cases: list[dict]) -> None:
    data_json = json.dumps(cases, ensure_ascii=False, indent=2)
    changed = sum(1 for c in cases if c["status"] == "changed")
    same = sum(1 for c in cases if c["status"] == "same")
    no_snap = sum(1 for c in cases if c["status"] == "no_snapshot")
    summary = json.dumps({"changed": changed, "same": same, "noSnapshot": no_snap})

    tsx = f"""import {{ useState }} from "react";
import {{
  Card, CardBody, CardHeader,
  DiffStats, DiffView,
  H1, H2, Pill, Row, Stack, Text,
  useHostTheme,
}} from "cursor/canvas";

type DiffLineData = {{ type: "added" | "removed" | "unchanged"; content: string; lineNumber?: number }};
type CaseData = {{
  name: string; label: string; status: string;
  additions: number; deletions: number; diffLines: DiffLineData[];
}};

const CASES: CaseData[] = {data_json};
const SUMMARY = {summary};

function StatusPill({{ status }}: {{ status: string }}) {{
  if (status === "changed")     return <Pill tone="warning">CHANGED</Pill>;
  if (status === "same")        return <Pill tone="success">SAME</Pill>;
  if (status === "no_snapshot") return <Pill tone="danger">NO SNAPSHOT</Pill>;
  return null;
}}

function CaseCard({{ c }}: {{ c: CaseData }}) {{
  const [open, setOpen] = useState(c.status === "changed");
  const theme = useHostTheme();

  return (
    <Card collapsible>
      <CardHeader
        trailing={{
          <Row gap={{8}} align="center">
            <StatusPill status={{c.status}} />
            {{c.status === "changed" && (
              <DiffStats additions={{c.additions}} deletions={{c.deletions}} />
            )}}
          </Row>
        }}
        onClick={{() => setOpen(!open)}}
      >
        <Text weight="medium">{{c.label}}</Text>
        <Text style={{{{ fontSize: 11, color: theme.tokens.textMuted }}}}>{{c.name}}</Text>
      </CardHeader>
      {{open && (
        <CardBody style={{{{ padding: 0 }}}}>
          {{c.status === "same" && (
            <Text style={{{{ padding: "12px 16px", color: theme.tokens.textMuted }}}}>
              No changes — rendered output matches snapshot.
            </Text>
          )}}
          {{c.status === "no_snapshot" && (
            <Text style={{{{ padding: "12px 16px", color: theme.tokens.textMuted }}}}>
              No snapshot yet. Run: pytest --snapshot-update tests/unit/test_prompt_snapshot.py
            </Text>
          )}}
          {{c.status === "changed" && (
            <DiffView lines={{c.diffLines}} language="markdown" showLineNumbers />
          )}}
        </CardBody>
      )}}
    </Card>
  );
}}

export default function PromptDiff() {{
  return (
    <Stack gap={{16}} style={{{{ padding: 24, maxWidth: 1100, margin: "0 auto" }}}}>
      <Stack gap={{4}}>
        <H1>Prompt Diff</H1>
        <Row gap={{8}} align="center">
          {{SUMMARY.changed > 0 && <Pill tone="warning">{{SUMMARY.changed}} changed</Pill>}}
          {{SUMMARY.same > 0    && <Pill tone="success">{{SUMMARY.same}} same</Pill>}}
          {{SUMMARY.noSnapshot > 0 && <Pill tone="danger">{{SUMMARY.noSnapshot}} no snapshot</Pill>}}
          <Text style={{{{ fontSize: 12 }}}}>snapshot vs current render · {{CASES.length}} test cases</Text>
        </Row>
      </Stack>
      {{CASES.map(c => <CaseCard key={{c.name}} c={{c}} />)}}
    </Stack>
  );
}}
"""
    _CANVAS_PATH.parent.mkdir(parents=True, exist_ok=True)
    _CANVAS_PATH.write_text(tsx, encoding="utf-8")
    print(f"\nCanvas written to: {_CANVAS_PATH}")


def main() -> None:
    print("Rendering prompts and computing diffs...")
    cases = _build_case_data()
    _write_canvas(cases)
    changed = sum(1 for c in cases if c["status"] == "changed")
    print("Done." + (f" {changed} case(s) changed." if changed else " All same."))


if __name__ == "__main__":
    main()
