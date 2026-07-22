"""Quick functional test for KBDerivedHints."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

from src.knowledge_base.kb_derived_hints import KBDerivedHints  # noqa: E402

hints = KBDerivedHints.from_json_file("data/knowledge_base/structured/ALS2SDTM_Mapping_Template_v1.0.json")

print("=== Direction 1: TESTCD pre-fill ===")
for mv in ["AIMS0101", "AHKYN", "LBORRES", "AETERM", "CMTRT"]:
    tc = hints.get_testcd_hint(mv)
    print(f"  {mv:20s} -> TESTCD={tc}")

print()
print("=== Direction 2: Domain examples ===")
for dom in ["LB", "AE", "QS", "FA"]:
    ex = hints.get_domain_examples(dom, n=3)
    print(f"  {dom}: {ex}")

print()
print("=== Direction 3: Disambiguation ===")
for av, dom in [("检查日期", "LB"), ("检查日期", "AE"), ("采样日期", "CP"), ("检查结果", "LB")]:
    sv = hints.get_disambiguation_hint(av, dom)
    print(f"  {av!r} in {dom} -> {sv}")

print()
print("=== Direction 4: MV prefix -> domain ===")
for mv in ["AETERM", "LBORRES", "CMTRT", "THTESTCD", "AIMS0101", "QSORRES"]:
    dom = hints.get_mv_prefix_domain(mv)
    print(f"  {mv:15s} -> {dom}")

print()
print("=== Direction 6: NOT_SUBMITTED tables ===")
for tbl in ["既往及现病史问询页", "不良事件问询页", "不良事件", "实验室检查"]:
    ns = hints.is_not_submitted_table(tbl)
    print(f"  {tbl!r:30s} -> NOT_SUBMITTED={ns}")

print()
print("=== Prompt section example ===")
section = hints.build_prompt_section(
    metadata_variable="AIMS0101",
    annotation_variable="评估结果",
    annotation_table="AIMS量表",
    domain="QS",
    n_examples=3,
)
print(section)
