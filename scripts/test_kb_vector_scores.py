"""Test KB vector matching scores and retrieval content.

Usage:
    # Set environment variables first:
    #   RAG_KB_DEFAULT_FILE=data/knowledge_base/structured/ALS2SDTM_TEST.json
    #   KB_ENABLE_VECTOR_MATCHING=true
    #   KB_VERBOSE=true
    #
    # Run:
    #   python scripts/test_kb_vector_scores.py
    #   python scripts/test_kb_vector_scores.py --min-score 0.80
    #   python scripts/test_kb_vector_scores.py --top-k 5 --min-score 0.75
"""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("RAG_KB_DEFAULT_FILE", "data/knowledge_base/structured/ALS2SDTM_TEST.json")
os.environ.setdefault("KB_ENABLE_VECTOR_MATCHING", "true")
os.environ.setdefault("KB_VERBOSE", "true")


TEST_CASES: list[dict] = [
    {
        "annotation_table": "不良事件",
        "annotation_variable": "不良事件名称",
        "metadata_variable": "AETERM",
        "metadata_table": "AE",
    },
    {
        "annotation_table": "受试者信息",
        "annotation_variable": "受试者筛选号",
        "metadata_variable": "SUBJID",
        "metadata_table": "SUBJECT",
    },
    {
        "annotation_table": "合并用药",
        "annotation_variable": "药物名称",
        "metadata_variable": "CMTRT",
        "metadata_table": "CM",
    },
    {
        "annotation_table": "生命体征",
        "annotation_variable": "收缩压",
        "metadata_variable": "SYSBP",
        "metadata_table": "VS",
    },
    {
        "annotation_table": "实验室检查",
        "annotation_variable": "检查项目",
        "metadata_variable": "LBTEST",
        "metadata_table": "LB",
    },
    {
        "annotation_table": "知情同意",
        "annotation_variable": "签署日期",
        "metadata_variable": "DSSTDAT",
        "metadata_table": "DS",
    },
]


def main():
    parser = argparse.ArgumentParser(description="Test KB vector matching scores")
    parser.add_argument(
        "--min-score", type=float, default=0.0, help="Minimum score threshold (default: 0.0 to show all)"
    )
    parser.add_argument("--top-k", type=int, default=5, help="Number of top results per query (default: 5)")
    parser.add_argument("--kb-file", type=str, default=None, help="Override KB file path")
    args = parser.parse_args()

    if args.kb_file:
        os.environ["RAG_KB_DEFAULT_FILE"] = args.kb_file

    from src.knowledge_base.llm_query_interface import LLMKnowledgeQueryInterface

    print("=" * 80)
    print("KB Vector Matching Test")
    print("=" * 80)

    qi = LLMKnowledgeQueryInterface()
    qi.set_verbose(True)

    stats = qi.get_kb_vector_stats()
    print("\n[Config]")
    print(f"  Embedding model: {stats['embedding_model']}")
    print(f"  Vector matching enabled: {stats['enabled']}")
    print(f"  Vectors loaded: {stats['vectors_loaded']}")
    print(f"  Num vectors: {stats['num_vectors']}")
    print(f"  Dimension: {stats['dimension']}")
    print(f"  Cache dir: {stats['cache_dir']}")
    print(f"  Test min_score: {args.min_score}")
    print(f"  Test top_k: {args.top_k}")

    if qi.ecrf_data is not None:
        print(f"  KB records loaded: {len(qi.ecrf_data)}")
    else:
        print("  [ERROR] No KB data loaded!")
        return

    print("\n" + "=" * 80)
    print("Running vector search for each test case...")
    print("=" * 80)

    for i, test_case in enumerate(TEST_CASES, 1):
        print(f"\n{'─' * 80}")
        print(f"[Test {i}/{len(TEST_CASES)}]")
        print(f"  Input: table={test_case['annotation_table']!r}  var={test_case['annotation_variable']!r}")
        print(f"         meta_table={test_case['metadata_table']!r}  meta_var={test_case['metadata_variable']!r}")
        print(f"{'─' * 80}")

        results = qi._vector_fuzzy_search(
            test_case,
            top_k=args.top_k,
            min_score=args.min_score,
            verbose=True,
        )

        if not results:
            print(f"  [NO MATCH] No results above min_score={args.min_score}")
        else:
            print(f"\n  Results ({len(results)} matches):")
            ecrf_data = qi._get_ecrf_exact_frame()
            for rank, (score, df_idx) in enumerate(results, 1):
                row = ecrf_data.loc[df_idx]
                print(f"    [{rank}] score={score:.4f}")
                print(f"        KB table: {row.get('annotation_table', '?')!r}")
                print(f"        KB var:   {row.get('annotation_variable', '?')!r}")
                print(f"        KB meta:  {row.get('metadata_variable', '?')!r}")
                print(f"        Domain:   {row.get('SDTM_Domain', '?')}")
                print(f"        SDTM:     {row.get('SDTM_Variable', '?')}")

        # Also show the full match pipeline result
        print("\n  [Full pipeline match]")
        full_result = qi._match_with_ecrf_data(test_case)
        if full_result["suggestions"]:
            for s in full_result["suggestions"][:3]:
                print(
                    f"    strategy={full_result.get('match_strategy', '?')!r}  "
                    f"conf={s['confidence']:.2f}  "
                    f"type={s['match_type']!r}  "
                    f"domain={s.get('domain', '?')}  "
                    f"sdtm={s.get('sdtm_variable', '?')}"
                )
        else:
            print("    [NO MATCH from pipeline]")

    print(f"\n{'=' * 80}")
    print("Score Distribution Analysis")
    print("=" * 80)

    all_scores = []
    for test_case in TEST_CASES:
        results = qi._vector_fuzzy_search(test_case, top_k=10, min_score=0.0, verbose=False)
        if results:
            all_scores.extend([s for s, _ in results])

    if all_scores:
        all_scores.sort(reverse=True)
        print(f"  Total scores collected: {len(all_scores)}")
        print(f"  Max:  {max(all_scores):.4f}")
        print(f"  Min:  {min(all_scores):.4f}")
        print(f"  Mean: {sum(all_scores) / len(all_scores):.4f}")

        thresholds = [0.95, 0.90, 0.85, 0.80, 0.75, 0.70]
        print("\n  Threshold analysis:")
        for t in thresholds:
            count = sum(1 for s in all_scores if s >= t)
            print(f"    >= {t:.2f}: {count}/{len(all_scores)} scores ({count / len(all_scores) * 100:.1f}%)")
    else:
        print("  [WARNING] No vector scores collected - check embedding configuration")


if __name__ == "__main__":
    main()
