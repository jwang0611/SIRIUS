# QC 修改率记录 schema（提案）

- 状态：**Proposal — 待 maintainer 签署**。本文档只定义"要记录什么"，不改动任何运行时代码。
- 关联：GitHub issue #12，Phase B 准入闸门"已定义 QC 修改率的记录 schema，但不在本 Issue 实现完整工作台"。
- 范围内：指标定义、字段清单、连接键、隐私约束、现状差距。
- 范围外：审阅工作台（B1/B2）、身份认证与电子签名、任何 cascade 语义或阈值调整、跨 session 的持久化统计服务。

签署之前，本文档中的字段名和枚举值都可以改；签署之后，它是 Phase B 实现和后续报表的契约。

## 1. 指标定义

### 1.1 审阅单元（review unit）

一个审阅单元 = **一条被呈现给审阅人的推荐**，粒度是：

```
(run_id, 四字段输入身份, recommendation_rank)
```

四字段输入身份沿用仓库既有定义（`src/evaluation/heldout.py` 的 `KEY_FIELDS`）：
`annotation_table`、`metadata_table`、`annotation_variable`、`metadata_variable`。

必须带 `recommendation_rank`，因为同一个变量可以有多条 `domain_recommendations`，
并且 standard 与 supplementary（SUPP）推荐会共用同一个四字段键。

### 1.2 主指标

```
QC 修改率 = 被审阅人修改的审阅单元数 / 实际被审阅的审阅单元数
         = count(review_outcome == "modified")
           / count(review_outcome in {"accepted", "modified", "rejected"})
```

- **分子**：审阅人把推荐的映射身份改成了别的值（归一化后不等）。映射身份是生产
  去重键使用的四元组 **`(domain, sdtm_variable, testcd, supp_variable)`**
  （`src/processors/normalizer.py` 与 `src/processors/sdtm_processor.py` 的
  `dedup_key`），必要时再加 `supp_dataset`。只比较 `(domain, sdtm_variable)`
  会系统性漏掉 SUPP 与 Findings About 的实质修正：生产契约把所有 SUPP 都写成
  `sdtm_variable="QVAL"`（`src/processors/postprocess.py`），真正区分映射的是
  `supp_variable`（QNAM）；FA 等映射还依赖 `testcd`。例如把
  `SUPPAE.QVAL/AECOM` 改成 `SUPPAE.QVAL/AEOTH`，二元组视角下"未修改"，
  四元组视角下是 `qnam_or_testcd_only` 修正。
- **分母**：审阅人**实际看过并给出结论**的单元。生成量不是分母（见 §5 G1）。
- `deferred`（打开过但未定论）默认**不计入分母**，也不计入分子。是否改为计入需要 maintainer 决策（§7）。

比较四元组是否"相同"时使用与生产匹配器一致的归一化，
避免大小写/空白造成假修改：`str.strip().upper()`（与 `corrections.py` 写入 parquet 时一致），
domain 比较前先 `strip_supp_prefix`（`src/config/domain_semantic_map.py`）；
`testcd` / `supp_variable` / `supp_dataset` 为空与缺失视为同值（归一化为 `None`）。

### 1.3 派生指标

| 指标 | 定义 |
| --- | --- |
| `domain_modification_rate` | `domain_changed == true` 的单元占分母的比例 |
| `variable_modification_rate` | `variable_changed == true` 的单元占分母的比例 |
| `supp_testcd_modification_rate` | `qnam_or_testcd_changed == true` 的单元占分母的比例（SUPP/FA 修正专属信号）|
| `rejection_rate` | `review_outcome == "rejected"` 占分母的比例 |
| `review_coverage` | 分母 / 该 run 产生的推荐总数（审阅覆盖度，不是修改率） |

三个分项率都直接建立在 R1 的逐分量布尔字段上（§2.1），**不依赖 `change_kind`**：
`change_kind` 把多分量变更压成 `mixed`，只适合展示分桶，无法还原
`domain+variable` 与 `variable+testcd` 这类组合，据它汇总分项率会算错。
分项率的分子只计 `true`；布尔为 `null`（`rejected` / `deferred`，见 §2.1
适用矩阵）不计入分子，但 `rejected` 按 §1.2 的主定义仍留在分母中。

### 1.4 分层（breakdown）

同一份原始记录必须支持以下切片，不额外落盘聚合结果：

- **按 domain**：用 `recommended_domain`（AI 推荐的域），不是修正后的域；否则无法回答"AI 在哪个域上错得多"。
- **按 cascade 来源**：`recommended_source` 与 `recommended_cascade_level`（1–4）。两者都要：
  Excel 的 `Source` 列把 Level 1 和 Level 2 都显示为 `KB`，也把 `FALLBACK` 折叠成 `LLM`
  （`_recommendation_source_excel_label()`），所以指标必须记原始的 `domain_rec["source"]`
  而不是 Excel 展示值（§5 G4）。
- **按批次**：`review_batch_id`（一次审阅动作）与 `run_id`（一次推荐生成）。
- **按置信度分箱**：`[0, 0.5)`、`[0.5, 0.7)`、`[0.7, 0.9)`、`[0.9, 1.0]`，用于校准分析
  （高置信度上的高修改率是最值得报警的信号）。

## 2. 记录 schema

三份记录。R2 是对现有文件的扩展，R1 / R3 是新增。全部落在 session 目录内，随 session 清理。

### 2.1 R1 `review_unit` — 审阅单元事件（新增）

建议文件：`data/output/sessions/<sid_…>/qc/qc_review_<sid_…>.parquet`，
写入走 `atomic_staging_path`（与 corrections 一致）。
一次审阅动作对同一单元只写一行；重复审阅追加新行并由 `reviewed_at` 决定最新状态。

> **不得使用 `session_manager.add_kb_file()` 注册，也不要放进
> `data/knowledge_base/sessions/`。** KB 注册不只是"纳入清理"：
> `src/web/routers/jobs.py` 会把 `get_kb_files()` 返回的每个文件快照后作为
> `extra_kb_files` 传给 direct-KB / RAG。R1 含 `annotation_table` /
> `metadata_variable`，恰好满足 RAG chunk loader 的必需列，一旦注册，下一次推荐
> 就会把 QC 审阅事件当成知识文档；direct loader 也会把这些缺少
> `SDTM_Domain` / `SDTM_Variable` 的行拼进匹配表，可能改变或打断推荐。
> `data/output/sessions/<sid_…>/` 本来就在 `_managed_session_dirs` 的递归清理
> 范围内；若需要显式逐文件跟踪，用通用的 `session_manager.add_file()`，它只做
> 归属与清理登记，不参与任何知识检索。

| 字段 | 类型 | 取值 / 说明 | 真源 | 现状 |
| --- | --- | --- | --- | --- |
| `schema_version` | str | 固定 `sirius-qc-review/v1` | 常量 | 缺失 |
| `review_batch_id` | str | 不透明 ID（uuid4 hex），一次审阅动作 | 前端/调用方生成 | 缺失 |
| `run_id` | str | 产生推荐的那次任务，取 `session_manager.session_dir_key(job_id)` | `job_manager` | 缺失（未透传到修正侧）|
| `session_ref` | str | `safe_session_key(session_id)`，`sid_` + sha256 | `src/infrastructure/session_key.py` | 已有（audit / 目录名）|
| `input_sha256` | str | `mapping_fingerprint(四字段)` | `src/evaluation/heldout.py` | 已有函数，未用于修正 |
| `annotation_table` | str | 结构性元数据 | 推荐输出 | 已有 |
| `metadata_table` | str | 结构性元数据 | 推荐输出 | 已有 |
| `annotation_variable` | str | 结构性元数据 | 推荐输出 | 已有 |
| `metadata_variable` | str | 结构性元数据 | 推荐输出 | 已有 |
| `recommendation_rank` | int | 1-based，`domain_recommendations` 中的位次 | 推荐输出 | 缺失 |
| `recommended_domain` | str | 大写域名，可为空（UNMAPPED）| `domain_rec["domain"]` | 已有 |
| `recommended_sdtm_variable` | str | 大写变量名（SUPP 契约下恒为 `QVAL`）| `domain_rec["sdtm_variable"]` | 已有 |
| `recommended_testcd` | str \| null | FA/Findings 的 TESTCD 语义码 | `domain_rec["testcd"]` | 已有 |
| `recommended_supp_dataset` | str \| null | 如 `SUPPAE` | `domain_rec["supp_dataset"]` | 已有 |
| `recommended_supp_variable` | str \| null | QNAM，SUPP 映射的真正区分键 | `domain_rec["supp_variable"]` | 已有 |
| `recommended_sdtm_variable_type` | str | `standard` / `supplementary` / `unknown` | `domain_rec["sdtm_variable_type"]` | 已有 |
| `recommended_source` | str | 原始来源标签：`KB` / `KB_NOT_SUBMITTED` / `RAG` / `LLM` / `UNMAPPED` / `FALLBACK` | `domain_rec["source"]`（不是 Excel 展示值）| 已有 |
| `recommended_cascade_level` | int \| null | 1–4 | 目前只在 audit JSONL | **部分缺失**（§5 G4）|
| `recommended_confidence` | float | 0.0–1.0，即 `domain_rec["score"]` | 推荐输出 | 已有 |
| `review_outcome` | str | `accepted` / `modified` / `rejected` / `deferred` | 审阅动作 | 缺失 |
| `final_domain` | str | 审阅后的域；`accepted` 时等于推荐值 | 审阅动作 | 部分已有（仅 modified）|
| `final_sdtm_variable` | str | 审阅后的变量 | 审阅动作 | 部分已有（仅 modified）|
| `final_testcd` | str \| null | 审阅后的 TESTCD | 审阅动作 | 缺失（§5 G9）|
| `final_supp_dataset` | str \| null | 审阅后的 SUPP 数据集 | 审阅动作 | 缺失（§5 G9）|
| `final_supp_variable` | str \| null | 审阅后的 QNAM | 审阅动作 | 缺失（§5 G9）|
| `domain_changed` | bool \| null | domain 分量归一化后不等；不适用时为 null（见适用矩阵）| 由 recommended/final 逐分量派生 | 缺失 |
| `variable_changed` | bool \| null | `sdtm_variable` 分量归一化后不等；不适用时为 null | 同上 | 缺失 |
| `qnam_or_testcd_changed` | bool \| null | `testcd` / `supp_dataset` / `supp_variable` 任一不等；不适用时为 null | 同上 | 缺失 |
| `change_kind` | str \| null | 展示用分桶标签，见下方派生规则；`deferred` 时为 null | 由上面三个布尔派生 | 缺失 |
| `reviewed_at` | str | ISO-8601 UTC | 服务端时钟 | 已有（`_corrected_at`）|
| `reviewer_ref` | str | 稳定的不可逆引用；无认证时固定 `unauthenticated` | Phase B 认证 | 缺失 |
| `review_source` | str | `webui` / `api` / `excel_import` | 入口 | 缺失 |

`review_outcome` 语义：

- `accepted`：审阅人确认推荐正确，未改动。
- `modified`：改动了映射身份四元组 `(domain, sdtm_variable, testcd, supp_variable)`
  （或 `supp_dataset`）中的任一分量。
- `rejected`：判定该推荐不应提交（映射为 `NOT SUBMITTED` 或删除该行）。
- `deferred`：呈现过、被打开过，但审阅人未给结论。

派生规则：recommended 与 final 两组四元组按 §1.2 归一化后**逐分量比较**，先落成
三个布尔字段（`domain_changed` / `variable_changed` / `qnam_or_testcd_changed`），
它们是分项率的真源，组合信息不丢失；`change_kind` 只是由这三个布尔派生的
展示用分桶标签。

四个派生字段按 `review_outcome` 的适用矩阵取值——`false` 表示"已比较且未变"，
`null` 表示"不适用/未定论"，二者不可混用：

| `review_outcome` | 三个 `*_changed` | `change_kind` |
| --- | --- | --- |
| `accepted` / `modified` | 逐分量计算（true / false）| 按下列规则派生 |
| `rejected` | 全部 null（final 侧无映射，无从比较）| 固定 `unmapped` |
| `deferred` | 全部 null（没有 final 结论）| null |

`accepted` / `modified` 时的 `change_kind` 派生：

- `none`：三个布尔全为 false（`accepted`）。
- `domain_only` / `variable_only`：恰好只有对应布尔为 true。
- `qnam_or_testcd_only`：仅 `qnam_or_testcd_changed` 为 true——SUPP 契约下
  `sdtm_variable` 恒为 `QVAL`，这一桶正是二元组视角会漏掉的实质修正。
- `mixed`：不止一个布尔为 true。`mixed` 单元照常计入每个为 true 的分项率
  （通过布尔字段，而不是解析这个标签）。

### 2.2 R2 corrections parquet 扩展（现有文件）

现有列（`src/web/routers/corrections.py` 的 `CORRECTION_COLUMNS`）：
`annotation_table`、`metadata_table`、`annotation_variable`、`metadata_variable`、
`SDTM_Domain`、`SDTM_Variable`、`_kb_source`、`_corrected_at`。

这份 parquet 的职责是"喂回 session KB"，不是度量真源。为了能把修正连回推荐，
建议追加下列列，全部允许为空：

| 追加字段 | 类型 | 说明 |
| --- | --- | --- |
| `_review_batch_id` | str | 连接到 R1 / R3 |
| `_run_id` | str | 连接到产生推荐的那次运行 |
| `_input_sha256` | str | 与 R1 一致的输入指纹 |
| `_old_domain` | str | 被替换掉的推荐域（目前只进 audit JSONL）|
| `_old_sdtm_variable` | str | 被替换掉的推荐变量 |
| `_old_testcd` | str \| null | 被替换掉的 TESTCD（四元组身份的一部分，§1.2）|
| `_old_supp_dataset` | str \| null | 被替换掉的 SUPP 数据集 |
| `_old_supp_variable` | str \| null | 被替换掉的 QNAM |
| `_old_source` | str | 产生该推荐的 cascade 来源 |
| `_old_cascade_level` | int \| null | 产生该推荐的 cascade 层级 |
| `_old_confidence` | float \| null | 产生该推荐的 `score` |

**实现约束（必须处理，否则会破坏既有 session）**：`_load_existing_corrections()` 在
任一 `CORRECTION_COLUMNS` 列缺失时抛 `CorrectionsStorageError`，接口返回 500。
如果直接把新列加进 `CORRECTION_COLUMNS`，所有已存在的 shard 会立刻变成"不可读"。
Phase B 必须把新列作为可选列处理（读取时缺失即补空），或者显式做一次带版本标记的迁移。

### 2.3 R3 `review_batch` — 批次汇总（新增）

一次审阅动作一行，用于记录分母的上界，并让"打开了但没做完"可见。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `schema_version` | str | 固定 `sirius-qc-review-batch/v1` |
| `review_batch_id` | str | 与 R1 对应 |
| `run_id` | str | 被审阅的推荐运行 |
| `session_ref` | str | `safe_session_key` |
| `opened_at` / `closed_at` | str | ISO-8601 UTC；未结束时 `closed_at` 为空 |
| `units_total` | int | 该 run 产生的推荐总数（分母的上界）|
| `units_presented` | int | 实际呈现给审阅人的单元数 |
| `units_reviewed` | int | `accepted + modified + rejected` |
| `units_modified` | int | `modified` |
| `review_status` | str | `open` / `completed` / `abandoned` |

`units_total` 可以直接取 audit 的 `batch_summary.total_variables`（已存在），
但它**只是生成量**，不能顶替分母（§5 G1）。

## 3. 身份与连接键

```
推荐输出 (JSON/Excel)  ──┐
                         ├─ (run_id, mapping_key, recommendation_rank) ─→ R1 review_unit
audit sdtm_mapping ──────┘                                                    │
                                                                              ├─ review_batch_id ─→ R3
corrections.parquet ─── (_run_id, mapping_key, _input_sha256) ────────────────┘
```

- **主键**：`(run_id, input_sha256, recommendation_rank)`。`input_sha256` 用
  `mapping_fingerprint()`，即四字段归一化后 JSON 序列化的 sha256。
- **归一化**：连接时同时接受 `mapping_key()`（宽松归一化）和 `deep_mapping_key()`
  （生产匹配器的深度归一化），与泄漏扫描的做法保持一致。
- **连回 cascade 来源与置信度**：R1 的 `recommended_source` / `recommended_cascade_level` /
  `recommended_confidence` 必须在**呈现推荐的时刻**由后端写入，不能在审阅时反查。
  反查不可靠：audit JSONL 是 best-effort（§5 G7），推荐 JSON 又缺 `cascade_level`（§5 G4）。
- **`run_id` 与 session 的关系**：`run_id` 只在 session 内唯一即可；跨 session 比较必须
  同时带 `session_ref`。
- **不要用文件名做键**：推荐输出文件名带模型后缀且可被覆盖，不是稳定标识。

## 4. 隐私约束

与 `src/evaluation/heldout.py`、`src/evaluation/offline_gate.py` 和 `AuditLogger` 的既有做法保持一致：

1. **只记录结构性元数据**：表名、变量名、SDTM 域、SDTM 变量、来源、置信度、时间戳。
   **禁止**记录 CRF 取值、注释自由文本、codelist 内容、`rationale`、prompt、模型响应。
2. **session 引用只用哈希**：所有落盘字段用 `safe_session_key()` 的 `sid_<sha256>`，
   原始 `X-Session-ID` 是 bearer 凭据，绝不进入文件名、日志或指标记录。
3. **审阅人身份**：当前没有认证系统，`reviewer_ref` 恒为 `unauthenticated`。
   Phase B 引入认证后也只存不可逆引用，不存姓名、邮箱或工号。
4. **聚合报表只输出计数与比率**，行级内容一律用 `input_sha256` 代替，
   与泄漏报告"只含哈希与计数"的约定一致。分层样本数低于阈值 N 时抑制输出（N 待定，§7）。
5. **保留期跟随 session**：R1/R3 与 corrections 一样注册进 session 清理。
   不新增独立于 session 的长期存储。
6. **不新增遥测或外部上报**（Locked Product Decision 3）。指标只在本地文件里，
   任何跨 session 汇总或导出都需要单独的、经批准的匿名化决策。
7. **不要把 audit JSONL 当作可解析的结构化真源**：所有字符串都过 `DataMasker`，
   形如 `001-001` 的 token 会变成 `[REDACTED]`（`SENSITIVE_PATTERNS` 会命中
   `[A-Z]*\d{3,4}-\d{3,4}`）。指标管道读到 `[REDACTED]` 必须容忍，且不得尝试反推原值。

## 5. Phase B 必须关闭的差距

以下都是对当前代码的核实结论，不是推测。

**G1 — 分母根本不存在。** 没有任何地方记录"审阅人看了多少条推荐"。
`POST /api/corrections` 只在**有修改时**才会被调用；被确认为正确的推荐不留任何痕迹。
audit 的 `batch_summary.total_variables` 是生成量，不是审阅量。
在 R1/R3 落地之前，任何"修改率"都只有分子，无法计算。

**G2 — 旧值没有进 parquet。** `CORRECTION_COLUMNS` 里只有 `SDTM_Domain` / `SDTM_Variable`
（写入的是**新值**），`CorrectionItem.old_domain` / `old_sdtm_variable` 只传给
`AuditLogger.log_correction()`，仅存活在 audit JSONL 中。
仅凭 parquet 无法判断某条修正到底改了什么，甚至无法判断它是否真的改变了推荐。

**G3 — 产生推荐的来源与置信度从未被记录到修正上。** `corrections.py` 调用
`log_correction()` 时 `old_result` 只有 `{"domain", "sdtm_variable"}`，
因此 audit 里的 `old_mapping.source` 恒为 `""`、`old_mapping.confidence` 恒为 `0.0`。
**按 cascade 来源和按置信度的分层目前完全不可算。**

**G4 — cascade 层级在推荐输出里不完整。** `_kb_suggestions_to_recs()` 只写
`source="KB"`，不写 `cascade_level`；只有 `RecommendationOrchestrator` 的 LLM 分支
显式设置 `rec["cascade_level"] = 4`。Excel 的 `Source` 列把 Level 1 与 Level 2
都显示为 `KB`。`cascade_level` 目前只可靠地存在于 audit JSONL 的 `sdtm_mapping` 条目。

**G5 — 没有 run / batch 标识。** 修正记录无法绑回产生它的那次运行，
因而无法关联模型、prompt 版本、KB 版本。跨版本的修改率趋势不可比。

**G6 — 同一四字段键的多条推荐不可区分。** 修正记录没有 `recommendation_rank`，
也没有 `sdtm_variable_type`；standard 与 SUPP 推荐会碰撞。
更进一步，`_deduplicate_corrections()` 按四字段键只保留最新一条，
反复修改同一变量的历史在 parquet 中被抹掉（只在 audit JSONL 里留痕）。

**G7 — audit JSONL 是 best-effort，不能作为指标唯一真源。**
`_append_entry()` 吞掉所有写入异常并只打一条 warning；`corrections.py` 里整段
audit 调用也包在 `try/except` 中。度量记录必须与 KB 写入同样是"失败即失败"的路径，
或者明确接受缺失并在报表里标注。

**G8 — 没有 WebUI 审阅入口。** v0.2.1 已移除在线编辑界面，
目前只有程序化的 `POST /api/corrections`（README §API）。
分母只有在 B1/B2 工作台落地后才会自然产生；在此之前 R1/R3 只能由
`review_source="api"` 或 `excel_import` 的批量导入填充。

**G9 — SUPP QNAM 与 TESTCD 完全不在修正流里。** `CorrectionItem` 与
`CORRECTION_COLUMNS` 都没有 `testcd` / `supp_dataset` / `supp_variable`，
推荐值与最终值两侧都是。生产契约把所有 SUPP 写成 `sdtm_variable="QVAL"`，
所以在今天的记录里 `SUPPAE.QVAL/AECOM → SUPPAE.QVAL/AEOTH` 这类修正
**不可见也不可补算**。§1.2 的四元组身份、`qnam_or_testcd_only` 桶以及
R1 的 `recommended_/final_` supp 字段都依赖 Phase B 把这三个分量带进
修正 API 与 R1 记录。

## 6. 建议的落地顺序（Phase B，不在本 issue 实现）

1. 在推荐生成时把 `run_id`、`recommendation_rank`、`cascade_level` 写进推荐输出
   （只加字段，不改 cascade 语义与阈值）。
2. 让修正 API 携带四元组身份的其余分量（`testcd` / `supp_dataset` /
   `supp_variable`，推荐值与最终值两侧，§5 G9），再扩展 corrections 的可选列
   （R2），并处理 `_load_existing_corrections()` 的向后兼容。
3. 新增 R1 / R3 的写入路径：与 corrections 相同的原子写；文件放
   `data/output/sessions/<sid_…>/qc/`（§2.1 —— 不进 KB 目录、不用
   `add_kb_file()`），需要逐文件跟踪时用 `session_manager.add_file()`。
4. 提供一个只读的本地汇总脚本（`scripts/` 下），输出计数与比率，遵守 §4 的脱敏约定。
5. B1/B2 工作台接入后，`review_source` 切换为 `webui`，分母开始真实可用。

## 7. 待 maintainer 决策

- `deferred` 是否计入分母？（默认提案：不计入。）
- 分层小样本抑制阈值 N 取多少？
- 是否需要跨 session 的 QC 趋势？如需要，匿名化导出与保留期必须单独批准（涉及 Locked Product Decision 3）。
- Phase B 是否引入审阅人认证与电子签名？这决定 `reviewer_ref` 是否有实际取值。
- QC 修改率是否纳入 release gate？（默认提案：先只观测、不设阈值，
  与 `docs/evaluation-release-gate.md` 的基线纪律分开管理。）
- 字段命名是否需要与 `docs/evaluation-release-gate.md` 的 `sirius-*/vN` 命名保持同一族
  （本提案已采用 `sirius-qc-review/v1`）。
