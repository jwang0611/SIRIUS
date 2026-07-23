# Spec Mapper Integration

## 概述

`spec_mapper` 模块已成功集成到 SIRIUS 项目中，提供从 ALS 文件到 SDTM Spec 模板的自动映射功能。

## 目录结构

```
sirius/
├── src/
│   └── spec_mapper/              # ALS to Spec 映射模块
│       ├── __init__.py           # SpecMapper 入口 & 编排
│       ├── helpers.py            # SUPP 行插入 & 条件映射辅助
│       ├── core/                 # 核心逻辑
│       │   ├── excel_reader.py   # 模板 Excel 读取
│       │   ├── excel_writer.py   # 模板 Excel 写入 & 公式生成
│       │   └── mapper.py         # ALS→SDTM 核心映射逻辑
│       ├── models/               # 数据模型
│       │   ├── als_record.py     # ALS 记录
│       │   ├── template_record.py # 模板记录
│       │   ├── supp_record.py    # SUPP 记录
│       │   ├── codelist_record.py # CODELIST 记录
│       │   └── conditional_record.py # 条件映射记录
│       ├── parsers/              # 解析器
│       │   └── sdtm_parser.py    # SDTM 映射解析
│       ├── utils/                # 工具函数
│       │   ├── config_loader.py  # 配置加载
│       │   ├── logger.py         # 日志
│       │   └── performance.py    # 性能监控
│       └── config/               # 配置
│           └── config.yaml       # 固定变量规则 & 外部编码变量定义
│
├── scripts/
│   └── generate_full_spec.py     # 端到端脚本 (CRF → ALS → Spec)
│
└── data/
    ├── knowledge_base/
    │   └── template_spec/        # SDTM 模板文件
    └── spec_output/              # 最终 Spec 输出
```

## 功能特性

### 核心映射功能
1. **简单 1:1 映射**: 直接映射 SDTM Variable 到模板变量
2. **一对多映射**: 多个 ALS 记录映射到同一变量，自动生成编号列表
3. **SUPP 域映射**: 自动解析 `SUPPXX.QVAL when QNAM=XXX` 并插入新行
4. **条件映射**: 处理 `XXTESTCD=XXX` 或 `XXTEST=XXX and XXCAT=XXX` 模式
5. **复合映射**: 支持 `/` 分隔的多类型映射

### 特色功能
- ✅ 保留 Excel 原有格式
- ✅ 高亮显示新增映射（黄色背景 + 斜体）
- ✅ 自动处理 SUPP 行插入和超链接维护
- ✅ 智能验证条件映射的域归属

## 填充规则详情

### 通用规则

- 所有工具自动填充的内容，如无特殊说明均以**黄色高亮**显示。

### CONTENT 表单

1. 统一在最后插入 SUPPxx 行并更新相关列。
2. 不在 SDTM template 里的非标准 Domain，会在最后补充一行并以黄色高亮加以提醒。
3. ALS 来源的 Domain，F 列使用 `metadata_table` 值**替换**模板值，并自动补充 `SDTM.DM`（非 DM 自身时）和 `SDTM.SV`（发现类 Domain）。
4. SUPPxx 行自动复制对应父域的 F 列值。

### CODELIST 表单

- **合并写入**：已有 `(domain, testcd_var, testcd_value)` 行仅补填空白 I/J 列；新增行在该 domain 块末尾**插入**（`insert_rows`），避免覆盖模板下方内容。
- **J 列 (EDC VAR)**：自动填充 `metadata_variable` 值。

### Domain 表单

1. 如无特别说明，所有来源于 ALS 文件的 SDTM/SUPP 变量，来源均统一更新为 `CRF`，填写格式如：`[RAW]DM.STUDYID`。
2. 针对 label 长度超过 40 个字符或者来源于多个 rawdata 的 SUPP 变量，会以**红色高亮**加以提示。
3. SDTM 变量来源于多个 rawdata 时，会在转换定义列加上英文分号 (`;`) 作为分隔符。
4. 去除 xxTESTCD & xxORRES 对应转换定义里的数字编号，并在 CODELIST 表单自动填充 ALS 文件里已有的 xxTESTCD（xxTEST 需用户自行定义）。
5. 带编码的 raw 变量目前通过 ALS 文件中的编码自动识别，仅在变量为单选时生成注释（多选不需要），统一在 H 列添加注释：`注：请根据rawdata里变量对应的编码描述进行赋值`。
6. 统一更新 MedDRA & WHODRUG 相关 SDTM & SUPP 变量。
7. 每个 domain 表单最下面用 Excel 语言加上 CONTENT 的链接。

### 固定 Variable 写法

| Variable | F 列 | H 列（转换定义） | 备注 |
|----------|------|-------------------|------|
| **STUDYID** | 指定 | `DM.STUDYID` | 除 DM 外所有 domain 表单统一 |
| **USUBJID** (DM) | 衍生 | 通过 `"-"` 连接 `STUDYID, SITEID, SUBJID` | 仅 DM 表单 |
| **USUBJID** (非 DM) | 指定 | `DM.USUBJID` | 除 DM 外统一 |
| **DOMAIN** | 指定 | `xx`（xx 为该 SDTM 表单名称） | |
| **xxSEQ** | 衍生 | `=CONCATENATE("按照变量 ",$D$10,"排序。 在每个USUBJID开始的时候从1开始重新编号 (循环地排序)")` | D10 引用 CONTENT H 列排序变量 |
| **xxTESTCD** | 指定 | `=HYPERLINK("#CODELIST!A1","赋值参考CODELIST表")` | E 列统一为 `(xxTESTCD)` |
| **xxTEST** | 指定 | `=HYPERLINK("#CODELIST!A1","赋值参考CODELIST表")` | E 列统一为 `(xxTEST)` |
| **xxDY** | 衍生 | 计算 `xxDY = (数值型 xxDTC - 数值型 DM.RFSTDTC)`；加 1 如果 ≥ 0；日期部分缺失则为空 | |
| **VISIT / VISITNUM** | 指定 | `=HYPERLINK("#VISIT!A1","转换定义参考VISIT表")` | 排除 TA/TD/TE/TI/TS/TV |
| **xxDTC** | — | 统一添加注释：`注：统一为ISO8601格式` | 包括 SUPP 变量 |
| **xxSTAT** | — | 统一添加注释：`若xxORRES为空，则赋值"未查"` | |
| **xxSPID / xxGRPID** | — | 统一添加注释：`注：统一为z2格式` | |

## 使用方式

### 方式 1: 端到端脚本 (推荐)

从 CRF JSON 生成完整的 SDTM Spec：

```bash
# 完整流程: CRF → ALS → Spec
python scripts/generate_full_spec.py \
  --json-file data/processed/crf_data.json \
  --template-file data/knowledge_base/template_spec/SDTM_template_IG3.2.xlsx \
  --output data/spec_output/final_spec.xlsx

# 使用现有 ALS 文件
python scripts/generate_full_spec.py \
  --als-file data/output/existing_als.xlsx \
  --template-file data/knowledge_base/template_spec/SDTM_template_IG3.2.xlsx \
  --output data/spec_output/final_spec.xlsx

# Dry run 预览
python scripts/generate_full_spec.py \
  --als-file data/output/als.xlsx \
  --template-file data/knowledge_base/template_spec/SDTM_template_IG3.2.xlsx \
  --output data/spec_output/result.xlsx \
  --dry-run
```

### 方式 2: Python API

在代码中直接使用：

```python
from src.spec_mapper import SpecMapper

# 初始化 Mapper
mapper = SpecMapper(
    als_file="data/output/als.xlsx",
    template_file="data/knowledge_base/template_spec/SDTM_template_IG3.2.xlsx",
    als_sheet="Sheet1"
)

# 执行映射
stats = mapper.process(
    output_file="data/spec_output/result.xlsx",
    highlight=True,
    dry_run=False
)

# planned（计划数量，旧字段语义不变）
print(f"✓ Planned {stats['updates']} cell updates, {stats['supp_records']} SUPP rows")
# actual（真实写入结果）
actual = stats["actual"]
print(f"✓ Written {actual['written']}/{actual['attempted']} "
      f"(skipped={actual['skipped']}, warnings={actual['warnings']}, errors={actual['errors']})")
```

> ⚠️ `stats['updates']` / `stats['supp_records']` 等顶层计数是**计划**（planned）数量。
> 真实成功写入的数量在 `stats['actual']['written']` 和逐阶段的 `stats['write_result']` 中，
> 详见下方「写入结果可观测性」。

### 方式 3: 便捷函数

一行代码完成映射：

```python
from src.spec_mapper import map_als_to_spec

stats = map_als_to_spec(
    als_file="data/output/als.xlsx",
    template_file="data/knowledge_base/template_spec/SDTM_template_IG3.2.xlsx",
    output_file="data/spec_output/result.xlsx"
)
```

## 写入结果可观测性

`process()` 会返回一个结构化、可 JSON 序列化的**实际写入结果**，用于区分「计划做多少」与「真正写成功多少」。

### stats 结构

```python
stats = {
    # ---- planned（映射计划数量；旧字段，语义不变，向后兼容）----
    "als_records": 120, "template_records": 1277,
    "updates": 42, "supp_records": 8, "conditional_records": 3,
    "codelist_records": 5, "unmatched_records": 1,
    "planned": {"cell_updates": 42, "supp_rows": 8, "unmatched_rows": 1,
                "conditional_mappings": 3, "codelist_records": 5},

    # ---- actual（真实写入结果的安全摘要）----
    "actual": {"attempted": 96, "written": 95, "skipped": 1,
               "warnings": 2, "errors": 0},

    # ---- write_result（逐阶段明细，见下）----
    "write_result": {"summary": {...}, "stages": {...}, "warnings": [...], "errors": [...]},
    "output_file": "data/spec_output/result.xlsx",
}
```

### 阶段（stage）

写操作按类型归入以下阶段，每个阶段独立记录 `attempted` / `written` / `skipped` / `warnings` / `errors`：

`cell_updates`、`supp_rows`、`unmatched_rows`、`conditional_mappings`、`codelist_records`、
`fixed_variable_rules`、`formulas_and_links`、`source_columns`、`external_coding`、`content_domains`、`styles`。

不变式：每个阶段 `attempted == written + skipped + len(errors)`。`written` **只在**对应 workbook mutation 成功后递增。

**真实 mutation 计数（no phantom write）**：`written` 反映真实发生的 workbook 变更，而非“调用未抛异常”。
例如 CODELIST 记录仅在插入新行或填充空白 I/J 单元格时计入 `written`；若该记录的行已存在且 I/J 已填，则为
no-op，记为 `skipped`（`code="codelist_unchanged"`）。经 `_guard` 的单次写操作以**返回的 mutation 计数**表达真实结果：
返回 `N>0` 记为 `record_written(N)`（批量方法如 wrap_text / Source 列 / 固定变量规则贡献全部 N），
返回 `0` 记为 `skipped`（`code="no_op"`）——`actual.written` 是真实 mutation 数，不是调用次数。

**重复运行幂等**：以第一次输出作为第二次运行的模板时，插入类写操作不会产生重复行——
`add_supp_to_content_sheet` 对已存在的 `SUPP{domain}` 行就地更新，`add_nonstandard_domain_to_content` /
`add_external_coding_variables` 跳过已存在项并返回真实插入数，CODELIST 走 merge/dedup，
`process_conditional_mappings` 按**完整列组**匹配复用（连续表头组合，如 `CRF_TESTCD+CRF_ORRES`），
覆写数据并清空陈旧行而非重复追加；列组按整体匹配而非单个表头名，因此同一 TEST sheet 同时存在
TESTCD 与 TEST 两组条件时，两组各自的 `CRF_ORRES` 列及数据都保留、互不覆盖（与原追加式布局一致）。

### 错误分类（recoverable vs 未知/致命）与逐项原子性

- **可恢复 = 预校验，不 = 捕获异常**。逐项写循环（单元格 / SUPP 行 / unmatched 行 / 条件映射 / CODELIST 记录）
  在执行**任何**破坏性操作（删除既有 SUPP 块、插行、清 hyperlink）之前，先校验目标存在性与非法字符
  （`contains_illegal_characters`，与 openpyxl 的 `ILLEGAL_CHARACTERS_RE` 同源）。不合法的项记录结构化
  error（`code="illegal_characters"`）且**零 mutation**；若某域的 SUPP 记录全部不合法，该域完全不被触碰
  （旧 SUPP 块保留）。因此“可恢复”结果绝不会与半成品行或已被破坏的旧数据并存。
- **`_guard` 单次写操作**只降级专用 `RecoverableWriteError`；可恢复状态来自返回计数（前置条件不满足返回 0），
  而非中途抛出的异常。
- **未知 / 致命**：写入过程中抛出的任何其它异常（含裸 `ValueError`、openpyxl `IllegalCharacterError`、
  `KeyError`、`OSError`、`RuntimeError` …）一律向上传播，使 Job 判定为 `failed`——绝不保存
  “计数不实的部分工作簿”，也不会用宽泛 `except` 把真实 bug 掩盖成“可恢复”。

### warnings / errors 安全约定（GxP / PHI）

`WriteIssue` 只包含安全字段：`code`、`stage`、`operation` 和 workbook 定位（`sheet` / `row` / `column`），
`detail` 仅为异常**类名**（如 `"RecoverableWriteError"`）。**不包含**绝对路径、原始临床值、API key/token、
Python traceback 或完整异常文本。

### 任务终态判定（Web 后台任务）

| 场景 | 终态 | 产物 |
| --- | --- | --- |
| 所有计划写入成功（`written == attempted`，无 error） | `completed` | Excel 可下载 |
| workbook 已保存，但存在写入失败或跳过（`written < attempted` 或有 error） | `completed_with_errors` | Excel **仍可下载**供人工复核 |
| workbook 无法打开 / 保存 / 产物不可用 | `failed` | 无产物 |

Job 状态额外暴露 `spec_attempted` / `spec_written` / `spec_skipped` / `spec_warnings` / `spec_errors` 安全摘要，
以及结构化问题清单：`spec_issues`（前 N 项，API payload 有上限）与 `spec_issues_total`（真实总数）。
完整、脱敏的问题清单持久化为文件（`output_issues`）并可经 `GET /api/jobs/{job_id}/download-issues` 下载。
若持久化失败（OSError），完整列表回退到 `spec_issues` payload 本身——任何被跳过/失败的写入项在任何情况下都
可查看，不会被静默丢弃；前端明确展示“显示 N / 共 M”，且仅在 `output_issues` 真实存在时渲染下载链接（绝无死链）。

可下载日志由专用 formatter 输出：脱敏绝对路径，且从不追加 `exc_info` / `stack_info`，因此同线程内任何
`logger.exception(...)` 都不会把服务器路径或内部堆栈写入用户可下载日志。

## 命令行参数

### `generate_full_spec.py`

```
必需参数:
  --template-file FILE      SDTM 模板文件
  --output FILE            输出 Spec 文件

输入选项 (二选一):
  --json-file FILE         CRF JSON 文件 (生成 ALS)
  --als-file FILE          现有 ALS 文件 (跳过 ALS 生成)

ALS 生成选项:
  --provider PROVIDER      AI 提供商 (openrouter/openai/google)
  --model MODEL            AI 模型名称
  --enable-kb              启用 RAG 知识库
  --language LANG          Prompt 语言 (en/cn)

映射选项:
  --als-sheet SHEET        ALS Sheet 名称（优先级：命令行参数 > ALS_DEFAULT_SHEET > 配置；默认 Sheet1）
  --highlight              高亮新映射 (默认: True)
  --no-highlight           禁用高亮
  --dry-run                预览模式
  --log-level LEVEL        日志级别 (DEBUG/INFO/WARNING/ERROR)
```

## 配置文件

配置文件位于 `src/spec_mapper/config/config.yaml`：

```yaml
# ALS 文件列映射
als_columns:
  table: "表"
  variable: "变量"
  variable_label: "变量名"
  format: "格式"
  sdtm_domain: "SDTM domain"
  sdtm_variable: "SDTM Variable"

# 模板文件列映射
template_columns:
  variable_name: "变量名称"
  variable_name_en: "Variable Name"
  transformation_def: "转换定义"
  transformation_def_en: "Transformation definition"

# 输出格式
output_format:
  prefix: "[RAW]"
  multi_item_format: "{index}. {value}"
  separator: "\n"
```

## 完整工作流

```
┌─────────────┐
│  CRF JSON   │
└─────┬───────┘
      │
      │ (generate_sdtm_recommendations.py)
      ↓
┌─────────────┐
│  ALS File   │ (SIRIUS 生成的 ALS)
└─────┬───────┘
      │
      │ (spec_mapper)
      ↓
┌─────────────┐
│ SDTM Spec   │ (填充好的说明文件)
└─────────────┘
```

## 集成到 Web 界面 (未来计划)

在 `src/web/app.py` 中添加新的 API 端点：

```python
@app.post("/api/generate-spec")
async def generate_spec(request: SpecRequest):
    """Generate SDTM Spec from ALS file"""
    job_id = uuid.uuid4().hex
    job_manager.create_job(job_id)
    start_spec_generation_job(
        job_id,
        request.als_file,
        request.template_file
    )
    return {"job_id": job_id}
```

## 性能指标

基于测试数据集（563 条 ALS 记录，1277 条模板记录）：

- **总执行时间**: ~23 秒
- **内存峰值**: ~155 MB
- **处理能力**: 
  - 166 个单元格更新
  - 231 个 SUPP 行插入
  - 19 组条件映射

## 故障排除

### 导入错误

```bash
# 确保在项目根目录
cd /path/to/sirius

# 测试导入
python -c "from src.spec_mapper import SpecMapper; print('OK')"
```

### 配置文件问题

```python
from src.spec_mapper import ConfigLoader

# 使用自定义配置
config = ConfigLoader("custom_config.yaml")
```

### 日志调试

```python
import logging
logging.basicConfig(level=logging.DEBUG)

from src.spec_mapper import SpecMapper
# 查看详细调试信息
```

## 依赖关系

`spec_mapper` 模块依赖：
- `openpyxl>=3.1.0` - Excel 文件操作
- `pandas>=2.0.0` - 数据处理
- `pyyaml>=6.0` - 配置文件解析
- `psutil>=5.9.0` - 性能监控

## 测试

```bash
# 单元测试
pytest tests/test_spec_mapper.py -v

# 覆盖率测试
pytest tests/test_spec_mapper.py --cov=src.spec_mapper --cov-report=html
```

## 版本历史

### v0.3.0 (Unreleased)

**实际写入统计、错误可观测性与真实模板端到端保护（Issue #12 A5）**
- 新增结构化写入结果模型 `WriteResult` / `StageWriteResult` / `WriteIssue`（`models/write_result.py`）
- `process()` 返回值同时包含 `planned` 与 `actual`，`actual.written` 来自真实成功写入，不再用 `len(updates)` 代表成功写入数
- 逐项可恢复的写入问题记录到结构化 `warnings` / `errors` 并继续处理，不再静默吞掉；结构化对象不泄漏路径、原始临床值、token 或 traceback
- 后台任务据实际写入结果判定 `completed` / `completed_with_errors` / `failed`；`completed_with_errors` 产物仍可下载
- Spec Job API / 任务 message / 可下载日志改为仅记录文件名，不再记录绝对路径或异常 traceback
- 新增基于真实 IG 3.2 / IG 3.4 模板的端到端测试（cell update、SUPP、QNAM/QVAL、CODELIST merge/insert、公式与超链接保持/生成、样式、生成高亮、合并单元格、重复运行去重、可恢复失败、致命失败）

**A5 复审加固**
- CODELIST 与经 `_guard` 的写操作按真实 mutation 计数：已满足的 CODELIST 记录记为 `skipped`（`codelist_unchanged`），消除 phantom write；`_guard` 对批量方法记 `record_written(N)`，`actual.written` 反映真实 mutation 数
- 插入路径重复运行幂等：`add_supp_to_content_sheet` 就地更新已存在 `SUPP{domain}`；`add_nonstandard_domain_to_content` / `add_external_coding_variables` 跳过已存在项并返回真实插入数；`process_conditional_mappings` 按完整列组匹配复用并清理陈旧行（TESTCD/TEST 混合条件的两组 `CRF_ORRES` 互不覆盖）；端到端断言 CONTENT/SUPP/CODELIST/条件列重跑不产生重复（IG 3.2 与 IG 3.4）
- 新增专用 `RecoverableWriteError` + 逐项预校验原子性：`_guard` 只降级该类型；逐项写循环在任何破坏性操作前预校验非法字符与目标存在性（`illegal_characters` 结构化 error、零 mutation；某域 SUPP 全部不合法时该域不被触碰）；写入中抛出的任何异常（含裸 `ValueError` / `IllegalCharacterError`）一律 `failed`，绝不保存计数不实或半成品的部分工作簿
- 结构化问题清单不再静默截断：新增 `spec_issues_total` 与完整清单文件 + `GET /api/jobs/{job_id}/download-issues`；持久化失败时完整列表回退到 payload；前端展示“显示 N / 共 M”，仅在文件存在时渲染下载链接
- 可下载日志改用专用 formatter：脱敏绝对路径并从不追加 `exc_info` / `stack_info`，`logger.exception(...)` 不会泄漏 traceback / 服务器路径（不修改共享 `LogRecord`）

### v0.2.0 (2026-03-11)

**Spec Mapper 增强**
- SUPP 变量长度：以数值写入并复制模板 `number_format`，保持 `$200` 格式一致
- SUPP 变量排序：按 CRF 原始行号 (`source_order`) 排序，不再按变量名字母序
- CODELIST 合并写入：已有行仅补填空白列；新增行在 domain 块末尾插入（`insert_rows`），避免覆盖模板内容
- CODELIST 新增 J 列 (EDC VAR)：填充 `metadata_variable` 值
- CONTENT F 列：ALS 来源域使用 `metadata_table` 替换模板值，自动补充 `SDTM.DM` 和 `SDTM.SV`
- CONTENT SUPPxx 行：自动复制父域 F 列值
- Domain sheet D10：动态 `INDEX-MATCH` 公式引用 CONTENT H 列排序变量
- 固定变量规则：VISIT/VISITNUM 支持 `exclude_domains` 配置（排除 TA/TD/TE/TI/TS/TV）
- 外部编码变量：长度以数值写入，格式与标准 SDTM 变量一致
- Excel 公式保护：含换行的混合内容自动剥离前导 `=`，防止公式移除错误
- 新增 `CodelistRecord` 模型，支持 `metadata_variable` 字段

### v0.1.0 (2025-12-11)
- 集成到 SIRIUS 项目
- 移除 GUI 依赖
- 改为相对导入路径
- 添加公开 API (SpecMapper, map_als_to_spec)
- 创建端到端脚本
- 更新依赖管理

## 贡献者

- SIRIUS Team
- 原 als2spec 项目团队

## 许可证

内部工具 - 版权所有
