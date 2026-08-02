"""
I/O Helpers Mixin
File loading, progress persistence, eCRF merging, and AI interaction logging.
"""

import hashlib
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

import pandas as pd

from src.processors.mixin_typing import SDTMProcessorHost
from src.utils.atomic_json import atomic_write_json

logger = logging.getLogger(__name__)


class IOHelpersMixin:
    """File I/O, progress checkpointing, and AI interaction logging."""

    # ------------------------------------------------------------------
    # Expected instance attributes (initialised by the host class):
    #   self._input_file          : Optional[str]
    #   self.debug                : bool
    #   self.log_ai_interactions  : bool
    #   self.generation_config    : GenerationConfig
    #   self.language             : str
    # ------------------------------------------------------------------

    def _get_existing_recommendations(self, output_file: str) -> dict[str, dict[str, list[dict[str, Any]]]]:
        """
        Load existing recommendations from output files to support resuming.
        """
        existing_recommendations: dict[str, dict[str, list[dict[str, Any]]]] = {}

        temp_file = output_file + ".tmp.json"
        if os.path.exists(temp_file):
            try:
                with open(temp_file, encoding="utf-8") as f:
                    temp_data = json.load(f)

                if isinstance(temp_data, dict) and "recommendations" in temp_data:
                    expected_model = getattr(self, "model_name", None)
                    if expected_model and temp_data.get("model_name") != expected_model:
                        raise ValueError("checkpoint model identity mismatch")
                    expected_context = getattr(self, "checkpoint_context", None)
                    if expected_context is not None and temp_data.get("checkpoint_context") != expected_context:
                        raise ValueError("checkpoint context mismatch")
                    data = temp_data["recommendations"]
                    timestamp = temp_data.get("timestamp", "unknown")
                    completed = temp_data.get("completed_pairs", "unknown")
                    print(
                        f"Resuming from temporary file (last saved: {time.ctime(timestamp) if isinstance(timestamp, (int, float)) else timestamp}, completed pairs: {completed})"
                    )
                else:
                    if getattr(self, "model_name", None):
                        raise ValueError("legacy checkpoint has no model identity")
                    data = temp_data
                    print("Resuming from temporary file (older format)")

                for rec in data:
                    table_name = rec.get("table_name", "")
                    if not table_name:
                        continue
                    existing_recommendations[table_name] = {}
                    domain_recs = rec.get("domain_recommendations", [])
                    for domain_rec in domain_recs:
                        var_name = domain_rec.get("variable_name", "")
                        if var_name:
                            if var_name not in existing_recommendations[table_name]:
                                existing_recommendations[table_name][var_name] = []
                            existing_recommendations[table_name][var_name].append(domain_rec)

                print("Loaded existing recommendations from a temporary checkpoint")
                return existing_recommendations
            except Exception as exc:
                print(f"Warning: Error reading temporary checkpoint ({type(exc).__name__})")
                print("Falling back to regular output files")

        json_file = output_file + ".json"
        if os.path.exists(json_file):
            try:
                with open(json_file, encoding="utf-8") as f:
                    data = json.load(f)

                for rec in data:
                    table_name = rec.get("table_name", "")
                    if not table_name:
                        continue
                    existing_recommendations[table_name] = {}
                    domain_recs = rec.get("domain_recommendations", [])
                    for domain_rec in domain_recs:
                        var_name = domain_rec.get("variable_name", "")
                        if var_name:
                            if var_name not in existing_recommendations[table_name]:
                                existing_recommendations[table_name][var_name] = []
                            existing_recommendations[table_name][var_name].append(domain_rec)

                print("Loaded existing recommendations from JSON output")
                return existing_recommendations
            except Exception as exc:
                print(f"Error reading existing JSON output ({type(exc).__name__})")

        excel_file = output_file + ".xlsx"
        if os.path.exists(excel_file):
            try:
                df = pd.read_excel(excel_file)

                for row in df.to_dict("records"):
                    table = row.get("表", "") or row.get("Metadata Table", "")
                    variable = row.get("变量", "") or row.get("Metadata Variable", "")
                    if not table or not variable:
                        continue

                    if table not in existing_recommendations:
                        existing_recommendations[table] = {}

                    if variable not in existing_recommendations[table]:
                        existing_recommendations[table][variable] = []

                    rec = {
                        "domain": row.get("SDTM_Domain", "") or row.get("SDTM Domain", ""),
                        "sdtm_variable": row.get("SDTM_Variable", "") or row.get("Representative Variable", ""),
                        "score": row.get("Score", 0.0),
                        "priority": row.get("Priority", 1),
                        "variable_name": variable,
                    }
                    existing_recommendations[table][variable].append(rec)

                print("Loaded existing recommendations from Excel output")
                return existing_recommendations
            except Exception as exc:
                print(f"Error reading existing Excel output ({type(exc).__name__})")

        print("No existing recommendation files found")
        return existing_recommendations

    def _save_progress_to_temp_file(
        self, output_file: str, table_recommendations: dict[str, dict[str, list[dict[str, Any]]]]
    ) -> None:
        """Save the current progress to a temporary file."""
        all_recommendations = []

        for table, variable_recs in table_recommendations.items():
            all_domain_recs = []
            for _variable_name, domain_recs in variable_recs.items():
                all_domain_recs.extend(domain_recs)

            all_domain_recs.sort(key=lambda x: x.get("score", 0), reverse=True)

            table_recommendation = {
                "table_name": table,
                "domain_recommendations": all_domain_recs,
                "original_mappings": [],
            }

            all_recommendations.append(table_recommendation)

        temp_data = {
            "recommendations": all_recommendations,
            "timestamp": time.time(),
            "completed_pairs": sum(len(vars) for vars in table_recommendations.values()),
            "version": "1.0",
            "model_name": getattr(self, "model_name", None),
            "checkpoint_context": getattr(self, "checkpoint_context", None),
        }

        temp_file = output_file + ".tmp.json"
        try:
            atomic_write_json(temp_file, temp_data)
        except Exception as exc:
            print(f"Warning: Could not save progress to temporary file ({type(exc).__name__})")

    # ------------------------------------------------------------------
    # EDC-system detection and raw-sheet merge helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _detect_edc_system(raw_excel_path: Path) -> str:
        """Return 'taimei' or 'bioknow' by inspecting the raw file's sheet names.

        Uses openpyxl in read-only mode so it only parses the workbook index,
        not the full cell data — fast even for large files.  Falls back to
        'bioknow' on any error.
        """
        try:
            import warnings

            import openpyxl

            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                wb = openpyxl.load_workbook(raw_excel_path, read_only=True, data_only=True)
                sheet_names = wb.sheetnames
                wb.close()
            if "FormItem" in sheet_names:
                return "taimei"
        except Exception:
            pass
        return "bioknow"

    @staticmethod
    def _dedup_mapping_df(mapping_df: pd.DataFrame) -> pd.DataFrame:
        """Keep the top-scored recommendation per (metadata_table, metadata_variable)."""
        mapping_df = mapping_df.copy()
        mapping_df["Score_num"] = pd.to_numeric(mapping_df["Score"], errors="coerce").fillna(0)
        deduped = (
            mapping_df.sort_values("Score_num", ascending=False)
            .drop_duplicates(subset=["metadata_table", "metadata_variable"], keep="first")
            .drop(columns=["Score_num"])
        )
        dup_count = len(mapping_df) - len(deduped)
        if dup_count > 0:
            print(f"🔄 去重: 移除 {dup_count} 条重复推荐，保留每个变量最高分结果")
        return deduped

    def _build_num_lookup(self, processed_excel_path: Path) -> dict[tuple[str, str], str] | None:
        """Build {(metadata_table, metadata_variable): num} from the processed Excel."""
        processed_df = pd.read_excel(processed_excel_path, dtype=str)
        processed_df.columns = [str(col).strip() for col in processed_df.columns]
        if "num" not in processed_df.columns:
            print("⚠️ processed Excel 文件缺少 num 列")
            return None
        return {
            (str(row.get("metadata_table", "")).strip(), str(row.get("metadata_variable", "")).strip()): str(
                row.get("num", "")
            ).strip()
            for row in processed_df.to_dict("records")
        }

    def _merge_bioknow(
        self,
        mapping_df: pd.DataFrame,
        raw_excel_path: Path,
        num_lookup: dict[tuple[str, str], str],
    ) -> pd.DataFrame | None:
        """Merge for 百奥知 4.1: join on eCRF sheet '编号' column."""
        ecrf_df = pd.read_excel(raw_excel_path, sheet_name="eCRF", dtype=str)
        ecrf_df.columns = [str(col).strip() for col in ecrf_df.columns]

        ecrf_columns = [
            "编号",
            "表名",
            "表",
            "标准表",
            "分类",
            "引用表",
            "变量名",
            "变量",
            "组件类型",
            "编码名称",
            "SAS导出格式",
        ]
        missing_cols = [col for col in ecrf_columns if col not in ecrf_df.columns]
        if missing_cols:
            print(f"⚠️ eCRF sheet 缺少列: {', '.join(missing_cols)}")
            return None

        ecrf_df = ecrf_df[ecrf_columns].copy()
        for col in ecrf_df.columns:
            ecrf_df[col] = ecrf_df[col].fillna("").astype(str).str.strip()

        print(f"✓ 已加载 raw Excel eCRF sheet (百奥知): {raw_excel_path} ({len(ecrf_df)} 行)")

        mapping_df = mapping_df.copy()
        mapping_df["num"] = mapping_df.apply(
            lambda r: num_lookup.get(
                (str(r.get("metadata_table", "")).strip(), str(r.get("metadata_variable", "")).strip()), ""
            ),
            axis=1,
        )
        mapping_df["编号"] = mapping_df["num"]

        mapping_columns = ["metadata_table", "annotation_table", "SDTM_Domain", "SDTM_Variable", "Score", "Source"]
        # Include any extra result columns present in the input (e.g. IG34_Check, Diff_Status)
        _extra = ["IG34_Check", "Reference_Domain", "Reference_Variable", "Diff_Status"]
        mapping_columns = mapping_columns + [c for c in _extra if c in mapping_df.columns]
        deduped = self._dedup_mapping_df(mapping_df)

        merged_df = ecrf_df.merge(deduped[["编号", *mapping_columns]], on="编号", how="left")
        merged_df = merged_df[ecrf_columns + mapping_columns]

        print(f"✓ 合并完成 (百奥知): {len(merged_df)} 行")
        return merged_df

    def _merge_taimei(
        self,
        mapping_df: pd.DataFrame,
        raw_excel_path: Path,
        num_lookup: dict[tuple[str, str], str],
    ) -> pd.DataFrame | None:
        """Merge for 太美: join FormItem+Forms on (SASDatasetName, SASFieldName).

        The Taimei raw Excel can have multiple forms (e.g. LBB, LBC, LBU …) all
        mapping to the same SASDatasetName (e.g. 'LB') with the same set of
        SASFieldNames.  The AI pipeline deduplicates on (metadata_table,
        metadata_variable) = (SASDatasetName, SASFieldName), so each unique field
        gets exactly one recommendation.  We propagate that recommendation back to
        *all* raw rows that share the same (SASDatasetName, SASFieldName) by joining
        directly on those two columns — not via positional 'num'.
        """
        try:
            import warnings

            import python_calamine  # noqa: F401 — ensure engine is registered
        except ImportError:
            print("⚠️ 太美 merge 需要 python-calamine: pip install python-calamine")
            return None

        import warnings

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            forms_df = pd.read_excel(raw_excel_path, sheet_name="Forms", dtype=str, engine="calamine")
            formitem_df = pd.read_excel(raw_excel_path, sheet_name="FormItem", dtype=str, engine="calamine")

        forms_df.columns = [str(c).strip() for c in forms_df.columns]
        formitem_df.columns = [str(c).strip() for c in formitem_df.columns]

        forms_keep = ["FormOID", "SASDatasetName"]
        formitem_keep = ["FormOID", "FormName", "SASFieldName", "ItemName", "DataFormat"]

        for cols, sheet in [(forms_keep, "Forms"), (formitem_keep, "FormItem")]:
            missing = [c for c in cols if c not in (forms_df.columns if sheet == "Forms" else formitem_df.columns)]
            if missing:
                print(f"⚠️ 太美 {sheet} sheet 缺少列: {', '.join(missing)}")
                return None

        raw_df = formitem_df[formitem_keep].merge(forms_df[forms_keep], on="FormOID", how="left")

        for col in raw_df.columns:
            raw_df[col] = raw_df[col].fillna("").astype(str).str.strip()

        # Add 1-based sequence number (display only — not used as join key)
        raw_df.insert(0, "num", [str(i) for i in range(1, len(raw_df) + 1)])

        print(f"✓ 已加载 raw Excel (太美): {raw_excel_path} ({len(raw_df)} 行)")

        mapping_columns = ["metadata_table", "annotation_table", "SDTM_Domain", "SDTM_Variable", "Score", "Source"]
        # Include any extra result columns present in the input (e.g. IG34_Check, Diff_Status)
        _extra = ["IG34_Check", "Reference_Domain", "Reference_Variable", "Diff_Status"]
        mapping_columns = mapping_columns + [c for c in _extra if c in mapping_df.columns]
        deduped = self._dedup_mapping_df(mapping_df)

        # Join on (SASDatasetName, SASFieldName) so that one recommendation is
        # propagated to ALL raw rows sharing the same dataset+field combination
        # (handles the common case where multiple forms map to one SAS dataset).
        #
        # mapping_columns already contains "metadata_table", so we must NOT include
        # it again as a join-key selection — that would create duplicate column names
        # and crash the merge.  Use a separate join-key list instead.
        _join_keys = ["metadata_table", "metadata_variable"]
        _data_cols = [c for c in mapping_columns if c not in _join_keys]
        merged_df = raw_df.merge(
            deduped[_join_keys + _data_cols],
            left_on=["SASDatasetName", "SASFieldName"],
            right_on=_join_keys,
            how="left",
        )
        # Drop the right-side join key columns (SASDatasetName / SASFieldName already carry this info)
        merged_df = merged_df.drop(columns=_join_keys, errors="ignore")
        # Restore metadata_table from SASDatasetName for downstream consumers
        merged_df["metadata_table"] = merged_df["SASDatasetName"]

        taimei_columns = ["num", "FormOID", "SASDatasetName", "FormName", "SASFieldName", "ItemName", "DataFormat"]
        merged_df = merged_df[taimei_columns + mapping_columns]

        matched = merged_df["SDTM_Domain"].notna() & (merged_df["SDTM_Domain"] != "")
        print(f"✓ 合并完成 (太美): {len(merged_df)} 行 (其中 {matched.sum()} 行有推荐结果)")
        return merged_df

    def _merge_with_ecrf_sheet(self, mapping_df: pd.DataFrame) -> pd.DataFrame | None:
        """Merge mapping results with the original raw CRF Excel.

        Auto-detects the EDC system (百奥知 4.1 or 太美) by inspecting the
        raw file's sheet names and delegates to the appropriate merge helper.
        """
        if not hasattr(self, "_input_file") or not self._input_file:
            print("⚠️ 无法合并 raw sheet: 未设置 input_file")
            return None

        try:
            input_path = Path(self._input_file)

            processed_excel_path = input_path.with_suffix(".xlsx")
            if not processed_excel_path.exists():
                print(f"⚠️ 无法找到 processed Excel 文件: {processed_excel_path}")
                return None

            num_lookup = self._build_num_lookup(processed_excel_path)
            if num_lookup is None:
                return None

            raw_dir = input_path.parent.parent / "raw"
            raw_excel_path = raw_dir / f"{input_path.stem}.xlsx"

            if not raw_excel_path.exists():
                print(f"⚠️ 无法找到 raw Excel 文件: {raw_excel_path}")
                return None

            edc_system = self._detect_edc_system(raw_excel_path)
            print(f"ℹ️ 检测到 EDC 系统: {'太美' if edc_system == 'taimei' else '百奥知 4.1'}")

            if edc_system == "taimei":
                return self._merge_taimei(mapping_df, raw_excel_path, num_lookup)
            else:
                return self._merge_bioknow(mapping_df, raw_excel_path, num_lookup)

        except Exception as exc:
            print(f"⚠️ 合并 raw sheet 时出错 ({type(exc).__name__})")
            return None

    def _log_ai_interaction(
        self: SDTMProcessorHost,
        table_name: str,
        variable_name: str,
        interaction_type: str,
        content: str,
        content_type: str,
        api_duration: float | None = None,
    ) -> None:
        """Log content-free AI interaction metadata to a separate file."""
        os.makedirs("data/output/logs", exist_ok=True)

        log_date = time.strftime("%Y%m%d")
        log_file = f"data/output/logs/ai_interactions_{log_date}.log"

        content_digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        log_content = [f"\n{'=' * 80}", f"AI MODEL {interaction_type}"]

        if interaction_type == "INPUT":
            log_content.append(f"Content length: {len(content)} characters")
            log_content.append(f"Content SHA-256: {content_digest}")
            log_content.append(
                f"Generation config: max_tokens={self.generation_config.max_output_tokens}, "
                f"temp={self.generation_config.temperature}, top_p={self.generation_config.top_p}, "
                f"top_k={self.generation_config.top_k}"
            )
        else:
            log_content.append(f"Content length: {len(content)} characters")
            log_content.append(f"Content SHA-256: {content_digest}")
            if api_duration is not None:
                log_content.append(f"API duration: {api_duration:.2f}s")
        log_content.append(f"{'=' * 80}")

        try:
            with open(log_file, "a", encoding="utf-8") as f:
                f.write("\n".join(log_content))
        except Exception as exc:
            print(f"Warning: Could not write AI interaction metadata ({type(exc).__name__})")
