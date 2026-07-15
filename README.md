# SIRIUS v1.0

> **SDTM Intelligent Recommendation & Inference Unified System** - 基于 RAG 技术的智能化 CRF 到 SDTM 映射系统

一个利用检索增强生成（RAG）技术，将临床研究表单（CRF）自动映射到 CDISC SDTM 标准的智能化系统。

SIRIUS 当前提供 **Web 端**前端，未来将扩展为三套前端形态，共享同一套 FastAPI 后端：

- **Web 端**（本仓库当前形态）—— 浏览器访问的响应式界面（侧边栏导航、中英文切换、暖色 / 亮色 / 暗色三主题），源码位于 `src/web/static/`。
- **Windows 桌面软件** 与 **macOS 桌面软件**（后续 PR 交付）—— 计划基于 Electron 封装，内置启动本地后端并加载同一 Web 界面，构建配置将随桌面端 PR 一同加入 `desktop/`。

## ✨ 核心功能

- **智能映射**：基于 RAG + LLM 的 SDTM 变量映射推荐
- **4 级级联预测**：KB 精确匹配 → KB 高置信度 → RAG 增强 → LLM 推理，逐级退出以减少 LLM 调用
- **确定性规则引擎**：变量名超长智能修正（查找最相似的标准变量而非简单截断）、域合法性校验
- **批量一致性校验**：MappingCritic 对全批次结果做跨记录一致性检查（同表域一致性、TESTCD 关联、变量名合法性）
- **持续学习循环**：用户修正通过 API 反馈到 Session KB，下次映射自动引用修正结果
- **审计辅助日志**：结构化 JSONL 记录每次映射操作的输入/输出/级联层级/时间；当前不宣称满足 Part 11 或完整 GxP 系统要求
- **数据脱敏**：LLM 调用前对常见 PHI/PII 模式（SSN、受试者 ID、DOB、邮箱、电话等）做尽力（best-effort）脱敏；当前正则以英文格式为主，本地化标识符（如身份证号）覆盖为后续项
- **Domain 智能推断**：基于 annotation_table 关键词自动推断 SDTM Domain
- **知识库匹配**：支持默认 KB 和用户上传的项目特定 KB
- **向量检索**：基于语义相似度的知识片段检索
- **桌面优先前端**：Web 端三步式操作流程，界面支持中英文切换与暖色 / 亮色 / 暗色三主题；当前部署形态按单用户本地工具支持
- **Session KB 隔离**：项目 KB 与修正按 Session 分目录存储；全局产物目录尚不支持可信的多用户服务端隔离
- **Spec 生成**：自动生成 SDTM 说明文档

## 🏗️ 项目结构

```
sirius/
├── app.py                            # FastAPI 应用入口
├── src/                              # 源代码
│   ├── clients/                      # AI 客户端
│   ├── config/                       # 配置
│   │   └── domain_semantic_map.py   # Domain 语义映射 & 变量集
│   ├── knowledge_base/               # 知识库管理
│   │   └── llm_query_interface.py   # KB 直接匹配
│   ├── infrastructure/                # 审计与数据保护基础设施层
│   │   ├── audit_logger.py          # 结构化审计辅助日志（JSONL）
│   │   └── data_masker.py           # PHI/PII 数据脱敏
│   ├── processors/                   # 数据处理器
│   │   ├── sdtm_processor.py        # SDTM 处理核心（编排层 + 4 级级联）
│   │   ├── catalog.py               # CatalogMixin — 标准目录加载
│   │   ├── domain_inference.py      # DomainInferenceMixin — Domain 推断
│   │   ├── postprocess.py           # PostprocessMixin — 后处理 & 验证
│   │   ├── deterministic_validator.py # 确定性规则引擎（变量名/域验证）
│   │   ├── mapping_critic.py        # 批量映射一致性校验
│   │   └── io_helpers.py            # IOHelpersMixin — 文件 I/O & 日志
│   ├── prompts/                      # 提示词模块（内容以 YAML 为准）
│   │   ├── templates/               # Prompt 模板 YAML (variable_mapping.yaml)
│   │   ├── rules/                   # Domain 规则 YAML (sdtm_rules.yaml)
│   │   ├── examples/               # 模式示例 YAML (pattern_examples.yaml)
│   │   ├── loader.py               # Cached YAML Loader
│   │   ├── sdtm_prompts_simple.py   # Prompt 动态构建
│   │   └── sdtm_rules.py           # 规则加载适配
│   ├── rag/                          # RAG 核心模块
│   │   ├── chunker.py               # 文档分块
│   │   ├── embeddings.py            # 向量嵌入
│   │   ├── prompt_augmenter.py      # 提示增强 + Session KB
│   │   └── retriever.py             # 向量检索
│   ├── spec_mapper/                  # Spec 映射器（ALS → SDTM Spec Excel）
│   │   ├── __init__.py              # SpecMapper 入口 & 编排
│   │   ├── helpers.py               # SUPP 行插入 & 条件映射辅助
│   │   ├── config/
│   │   │   └── config.yaml          # 固定变量规则 & 外部编码变量定义
│   │   ├── core/
│   │   │   ├── excel_reader.py      # 模板 Excel 读取
│   │   │   ├── excel_writer.py      # 模板 Excel 写入 & 公式生成
│   │   │   └── mapper.py            # ALS→SDTM 核心映射逻辑
│   │   ├── models/                  # 数据模型
│   │   │   ├── als_record.py        # ALS 记录
│   │   │   ├── codelist_record.py   # CODELIST 记录
│   │   │   ├── supp_record.py       # SUPP 记录
│   │   │   ├── conditional_record.py # 条件映射记录
│   │   │   └── template_record.py   # 模板记录
│   │   ├── parsers/
│   │   │   └── sdtm_parser.py       # SDTM 映射解析
│   │   └── utils/                   # 配置加载 & 日志
│   └── web/                          # Web 服务
│       ├── routers/                 # API 路由（upload/files/jobs/session/spec_mapper/corrections）
│       ├── security.py              # 速率限制 & 安全
│       ├── session_manager.py       # Session 管理
│       ├── tasks.py                 # 后台任务
│       └── static/                  # 前端资源
├── scripts/                          # 命令行脚本
│   ├── convert_als2sdtm.py          # ALS 转换
│   ├── extract_ecrf_sheet.py        # eCRF 提取
│   ├── generate_full_spec.py        # Spec 生成
│   ├── generate_sdtm_recommendations.py  # 推荐生成
│   └── enhance_sdtm_spec_kb.py      # KB 增强
├── data/
│   ├── knowledge_base/
│   │   ├── documents/               # KB 原始文件
│   │   │   ├── standards/           # SDTM 标准文档
│   │   │   └── *.xlsx               # ALS 示例/模板
│   │   ├── structured/              # 预处理的 parquet
│   │   └── sessions/                # 用户 Session KB（自动清理）
│   ├── output/                      # ALS 输出
│   ├── processed/                   # 处理后的 JSON
│   └── spec_output/                 # Spec 输出
├── env_template.txt                  # 环境变量模板
├── requirements.txt                  # Python 依赖
└── run_web.bat                       # 启动脚本
```

## 📦 快速开始

### 1. 安装

```bash
# 克隆项目
git clone https://gitlab.qilu-pharma.com/mountain-high/sirius.git
cd sirius

# 创建虚拟环境
python -m venv venv
venv\Scripts\activate  # Windows
# source venv/bin/activate  # Linux/macOS

# 安装依赖
pip install -r requirements.txt
```

### 2. 配置

复制 `env_template.txt` 为 `.env` 并编辑：

```bash
# AI 模型配置
OPENROUTER_API_KEY=your_api_key
OPENROUTER_BASE_URL=https://ai-api.qilu-pharma.com/v1
DEFAULT_MODEL=google/gemini-3-flash-preview

# 知识库配置
RAG_KB_DEFAULT_FILE=xxx.parquet

# 级联预测阈值（可选，调整 LLM 调用量）
CASCADE_KB_HIGH_CONF=0.85    # Level 2: KB 高置信度阈值，达到此分数跳过 LLM
CASCADE_RAG_HIGH_CONF=0.70   # Level 3: RAG 增强阈值，达到此分数跳过 LLM
KB_MIN_CONFIDENCE=0.50       # KB 中间置信度下限，低于此值不返回 KB 建议

# API 速率限制（可选）
RATE_LIMIT_AI_JOB=10/minute
```

### 3. 启动

```bash
# macOS / Linux
./start.sh

# macOS / Linux 关闭服务
./stop.sh

# Windows
run_web.bat

# 或手动启动
python -m uvicorn app:app --host 0.0.0.0 --port 8000 --reload
```

通过访问 http://localhost:8000

`start.sh` 会自动探测 `venv` / `.venv`，检查 Python 版本（需 3.11+），在缺少虚拟环境时创建 `venv`，并按 `requirements.txt` 安装缺失依赖。可通过环境变量覆盖启动参数：

```bash
HOST=127.0.0.1 PORT=8080 RELOAD=0 OPEN_BROWSER=0 ./start.sh
```

首次安装会先使用 `requirements.txt` 配置的软件源；该源不可用时，`start.sh` 默认回退到官方 PyPI。
如需禁止回退，请使用 `PIP_FALLBACK_INDEX_URL= ./start.sh`。

关闭指定端口的服务时使用同样的 `PORT`：

```bash
PORT=8080 ./stop.sh
```

## 🌐 Web 界面使用

### Step 1: 上传 & 预处理

| 上传类型 | 说明 |
|---------|------|
| ALS 原始文件 | CRF 数据定义 Excel，提取后生成 JSON |
| ALS2SDTM 示例 | 历史映射文件，转换为 KB 供后续匹配 |

### Step 2: AI 推荐

1. 选择处理后的 JSON 文件
2. 选择语言（中文/英文）和 AI 模型
3. 点击"生成推荐"

**模型设置（左下角 ⚙️）**：侧边栏左下角的「模型设置」支持自主配置 LLM Provider。内置 OpenRouter / OpenAI / DeepSeek 预设（也可选"自定义"），可分别设置 Base URL、Model ID 和 API Token。配置仅保存在当前浏览器（localStorage），随任务请求发送、服务器不持久化。

安全约束：

- **密钥不外泄**：服务器回退密钥（`OPENROUTER_API_KEY`）只会发往服务器自身配置的默认 endpoint。选择非默认 Provider 或自定义 Base URL 时，必须在设置中填写自己的 API Token，否则请求被拒绝（422）。
- **自定义 endpoint 白名单**：自定义 Base URL 的主机须在允许列表内——内置 provider 主机 + 服务器 `OPENROUTER_BASE_URL` 主机，管理员可通过环境变量 `SIRIUS_LLM_ALLOWED_HOSTS`（逗号分隔）为内网 / 本地模型网关追加可信主机；云元数据地址（`169.254.x`）一律拒绝。

注意：RAG 向量化仍使用服务器端环境变量。

**处理流程（4 级级联预测）**：
```
变量 → Level 1: KB 精确匹配 (score ≥ 0.95) → 直接采纳
     → Level 2: KB 高置信度 (score ≥ 0.85) → 快速验证后采纳
     → Level 3: RAG 增强匹配 (score ≥ 0.70) → RAG 结果采纳
     → Level 4: LLM 完整推理 → 确定性验证 → 结果
```

### Step 3: 生成 Spec

1. 上传 QC 后的 ALS2SDTM 文件
2. 选择 SDTM 模板（v3.2 与 v3.4 模板并存，下拉自动列出 `template_spec/` 下含 "template" 的文件）
3. 点击"生成 Spec"

> **模板版本**：`data/knowledge_base/template_spec/` 下放置 `SDTM_template_IG3.2.xlsx`（IG 3.2）与 `SDTM_template_IG3.4.xlsx`（IG 3.4）。两者结构兼容，代码按列索引写入；v3.4 无 `VISIT` 表（VISIT/VISITNUM 超链接自动跳过）、class 列为英文（findings 判定同时识别 `发现`/`Findings`）。
>
> SpecMapper 会读取模板 `CONTENT!B4` 的 SDTM 版本自动选择配置：IG 3.4 用 `src/spec_mapper/config/config_ig34.yaml`（`_extends` 继承基础配置，仅追加 `source_label_overrides`），把 Source 列的 `指定/衍生` 渲染为英文 `Assigned/Derived`；IG 3.2 沿用 `config.yaml`（中文），互不影响。

## 🔧 命令行使用

### 生成 SDTM 推荐

```bash
python scripts/generate_sdtm_recommendations.py \
    --json-file data/processed/your_file.json \
    --model google/gemini-3-flash-preview \
    --language cn \
    --enable-rag
```

### 转换 ALS2SDTM 示例

```bash
python scripts/convert_als2sdtm.py \
    --input data/knowledge_base/documents/ALS2SDTM_example.xlsx \
    --sheet "Mapping" \
    --output-dir data/knowledge_base/structured
```

### 生成完整 Spec

```bash
python scripts/generate_full_spec.py \
    --als-file data/output/als2sdtm.xlsx \
    --template-file data/knowledge_base/template_spec/SDTM_template_IG3.2.xlsx \
    --output data/spec_output/final_spec.xlsx
```

## 🧠 Domain 推断系统

### 推断优先级

| 优先级 | 数据来源 | 匹配方式 | 示例 |
|--------|----------|----------|------|
| 1 | `annotation_table` | 精确匹配 `CHINESE_TABLE_DOMAIN_MAP` | "不良事件" → AE |
| 2 | `annotation_table` | 关键词匹配 `ANNOTATION_KEYWORD_DOMAIN_MAP` | "超声心动图" in "UCG超声心动图" → CV |
| 3 | `annotation_table` | 通用关键词 `DOMAIN_KEYWORDS` | "实验室" in "实验室检查" → LB |
| 4 | `annotation_variable` | 语义匹配 | "不良事件名称" → AE |
| 5 | `metadata_table` | 表代码映射 | "VS" → VS |

### Domain 验证

系统自动验证 LLM 推荐的 Domain 是否有效：

```python
VALID_SDTM_DOMAINS = {
    "AE", "CE", "CM", "CV", "DA", "DD", "DM", "DS", "DV", "EC", "EG", "EX",
    "FA", "HO", "IE", "IS", "LB", "MB", "MH", "MI", "MO", "MS", "PC", "PE",
    "PP", "PR", "QS", "RELREC", "RP", "RS", "SC", "SE", "SM", "SR", "SS",
    "SU", "SV", "TA", "TD", "TE", "TI", "TR", "TS", "TU", "TV", "VS",
}
```

如果 LLM 返回无效 Domain（如 "CC"），系统会：
1. 自动纠正为推断的 `target_domain` 
2. 降低置信度分数（× 0.7，最高 0.6）
3. 在输出中标记 `invalid_domain_corrected: true`

### 扩展关键词映射

在 `src/config/domain_semantic_map.py` 中添加自定义映射：

```python
# 精确表名映射
CHINESE_TABLE_DOMAIN_MAP = {
    "不良事件": "AE",
    "妊娠检测": "LB",  # 新增
    ...
}

# 关键词映射（支持模糊匹配）
ANNOTATION_KEYWORD_DOMAIN_MAP = {
    "超声心动图": "CV",
    "肌酐清除率": "LB",  # 新增
    "吸烟史": "SU",      # 新增
    ...
}
```

## 📚 知识库架构

### KB 匹配优先级

| 级别 | 匹配方式 | 置信度 |
|------|---------|--------|
| 1 | 精确匹配（四字段完全一致） | 1.0 |
| 2 | 标注字段匹配（annotation_table + annotation_variable） | 0.9 |
| 3 | 模糊相似度匹配 | ≤0.8 |

### KB 来源

- **默认 KB**：`RAG_KB_DEFAULT_FILE` 配置的全局知识库
- **Session KB**：用户上传的项目特定 KB（自动隔离、自动清理）
- **用户修正 KB**：通过 `POST /api/corrections` 提交的修正记录（session 级别，最高优先级）

Session KB 会同时用于：
- 直接匹配（`LLMKnowledgeQueryInterface`）
- RAG 向量检索（`RAGPromptAugmenter`）

### 持续学习循环

主路径（Phase 2 规划）：**用户 QC 后的 ALS2SDTM 文件即"修正成果"**，在生成 Spec 时自动归档成项目 KB：

```
用户生成 Spec 时上传 QC 后的 ALS2SDTM 文件 + project_name
  → 系统自动解析并按项目名归档成 session KB（source="project:{name}"）
  → 下次同 session 的映射自动引用该项目 KB（优先级高于 CDISC 默认 KB）
  → 项目越多，KB 越丰富，系统越用越准
```

辅助路径（程序化）：`POST /api/corrections` 端点仍可供 CI/脚本化场景批量提交单变量修正，记录存入 session 级 parquet，source="correction"，confidence=1.0。目前无 WebUI 包装（v0.2.1 已移除在线编辑界面）。

## 🛡️ 审计与数据保护基础设施

> 当前实现提供面向审计就绪方向的技术基础，但缺少唯一用户身份、防篡改审计链、电子签名与验证包，不应据此宣称系统已满足 21 CFR Part 11 或完整 GxP 合规要求。

### 审计日志

每次映射操作记录为结构化 JSONL（`data/audit_logs/audit_{session_id}.jsonl`）：

| 字段 | 说明 |
|------|------|
| `timestamp` | UTC 时间戳 |
| `session_id` | 会话 ID |
| `operation` | 操作类型（sdtm_mapping / batch_summary / mapping_correction） |
| `cascade_level` | 级联层级（1=KB精确, 2=KB高置信度, 3=RAG, 4=LLM） |
| `input` | 输入变量元数据 |
| `output` | 映射结果（domain, sdtm_variable, source, confidence） |

### 数据脱敏

在发送数据到 LLM API 之前，自动识别并脱敏以下敏感信息：
- SSN 格式（XXX-XX-XXXX）
- 受试者标识（SUBJID、USUBJID 模式）
- 出生日期（DOB 上下文关联）
- 邮箱地址和电话号码

> ⚠️ 当前脱敏正则以英文/美式格式为主，尚未覆盖本地化标识符（如中国大陆 18 位身份证号、国内手机号格式、中文姓名）。在这些格式下不应假设已完成脱敏；本地化模式为待办项。

### MappingCritic 一致性校验

批量处理完成后自动运行以下跨记录一致性检查：

| 检查项 | 严重度 | 说明 |
|--------|--------|------|
| `same_table_domain_consistency` | warning | 同一 CRF 表的变量是否映射到过多不同域 |
| `findings_testcd_coherence` | warning/info | Findings 域变量是否有对应的 TESTCD |
| `variable_name_validity` | error | 变量名是否超过 8 字符或包含空格 |
| `domain_validity` | error | 域是否在合法 SDTM 域列表中 |
| `duplicate_mappings` | info | 是否存在重复映射 |

## 🔒 Session 管理

- 每个用户通过 `sessionStorage` 获得唯一 Session ID
- 上传的文件和 KB 存储在 `sessions/{session_id}/` 目录
- 关闭浏览器时自动清理用户文件
- 服务器每 8 小时清理过期 Session 目录

## 🤖 Prompt 结构

### 动态内容

| 组件 | 说明 |
|------|------|
| `{sdtm_rules}` | 根据推断的 Domain 动态选择相关规则 |
| `{domain_variables_section}` | 动态生成相关 Domain 的变量列表（从 `sdtm_spec_enhanced.json` 加载） |
| `{domain_examples_section}` | 根据变量模式（日期/名称/结果等）选择相关示例 |
| `{language_instruction}` | 中文/英文指令 |

### 关键约束

Prompt 中强制要求 LLM：
1. **有效 Domain**：只能选择预定义的 SDTM Domain，不能发明新 Domain
2. **变量长度**：所有变量名 ≤ 8 字符
3. **SUPP 规范**：`sdtm_variable="QVAL"`，`supp_variable` 为 QNAM 值
4. **纯变量名**：`sdtm_variable` 必须是纯变量名（如 `FAORRES`），不可嵌入 "when" 子句；条件信息通过 `testcd`、`supp_variable` 字段传递

## 📊 数据格式

### 输入 JSON

```json
[
  {
    "metadata_table": "AE",
    "metadata_variable": "AETERM",
    "annotation_table": "不良事件",
    "annotation_variable": "不良事件名称"
  }
]
```

### 输出 Excel

| 列名 | 说明 |
|------|------|
| 表名 | annotation_table |
| 表 | metadata_table |
| 变量名 | annotation_variable |
| 变量 | metadata_variable |
| SDTM_Domain | 映射的 SDTM 域 |
| SDTM_Variable | 映射的 SDTM 变量 |
| Score | 置信度分数 |
| Source | 来源（KB/RAG/LLM） |

## 🔍 故障排除

### API 调用失败

```bash
# 检查 API 配置
echo %OPENROUTER_API_KEY%
```

### KB 匹配无结果

确保 `.env` 中配置了默认 KB：
```bash
RAG_KB_DEFAULT_FILE=xxx.parquet
```

或通过 Web 界面上传项目特定的 ALS2SDTM 示例文件。

### 列名不匹配

ALS2SDTM 示例文件必须使用正确的列名（区分大小写）：
- `SDTM_Domain`
- `SDTM_Variable`

## 🗺️ 路线图

本项目参考 SATA（BeOne Medicines）和 ClinAgent（Jaime Yan）两篇论文的架构理念，按 4 个阶段逐步增强。

> 📋 **产品评审与执行路线**: 查看 [`PRODUCT_REVIEW_OPTIMIZATION_PLAN.md`](./PRODUCT_REVIEW_OPTIMIZATION_PLAN.md)。

### Phase 1: 快速收益 -- 已完成

- [x] **级联预测策略正式化** — 4 级级联退出，减少 40-60% LLM 调用
- [x] **确定性规则引擎加强** — 智能变量名修正（查找最相似标准变量而非截断）
- [x] **审计就绪基础** — 结构化 JSONL 审计辅助日志 + 英文格式 PHI/PII 尽力脱敏；身份、防篡改与本地化覆盖仍在后续路线图中
- [x] **MappingCritic 一致性校验** — 批量映射结果跨记录一致性检查
- [x] **持续学习循环** — 用户修正 API → Session KB → 自动回注

### Phase 2: 核心改进 -- 已完成

- [x] **Excel 置信度与来源可视化** — 导出文件中对 Score 列（绿 ≥0.9 / 黄 ≥0.7 / 红 <0.7）和 Source 列（KB/RAG/LLM/correction/project）进行颜色编码，附带 Legend 表单
- [x] **Spec 生成时自动回注项目 KB** — `/api/spec-mapper/run` 新增 `project_name` 字段，用户 QC 后的 ALS2SDTM 自动解析、打 `project:{name}` 标签写入 session KB，替代原在线编辑流程
- [x] **Excel AI 推荐 vs 参考对比列** — 当 session 已有项目 KB 或参考 ALS 时，导出 Excel 自动追加 `Reference_Domain`/`Reference_Variable`/`Diff_Status` 列与对应颜色
- [x] **MappingCritic Excel 集成** — 一致性问题输出到独立 `Consistency_Issues` sheet + 主表行内 `Critic_Flag` 列与彩色边框 + IG34_Check 列校验

### Phase 3: 架构升级

- [x] **Prompt YAML 外部化**（Skill 化第一步）
  - 规则、模板、示例迁移为版本化 YAML 文件（`sdtm_rules.yaml` / `variable_mapping.yaml` / `pattern_examples.yaml`）
  - 规则/Prompt 更新不需要改代码，Cached YAML Loader 自动加载
  - 新增 Prompt CI 验证脚本（7 项静态检查）
- [ ] **Skill Router 完整化**（借鉴 ClinAgent "Thin MCP, Thick Skills"）
  - 每个 Skill 可独立单元测试（YAML fixture → 断言输出）
  - 支持 Skill 热加载与动态编排
- [ ] **KB 分层** — 知识库按来源分类（CDISC 标准 / 公司规则 / 历史映射 / 用户修正），支持优先级和权重
- [ ] **Best-of-N 选择**（借鉴 SATA）— 低置信度场景生成多候选映射，用规则引擎评分选优
- [x] **CDISC 合规自动检查** — IG 3.4 标准变量校验（63 域）、MappingCritic 12 项一致性检查、DeterministicValidator 域/变量/前缀校验

### Phase 4: 未来方向

- [ ] **动态代码生成**（借鉴 SATA Module 3）
  - 从 SDTM Spec 自动生成 SAS/Python 转换代码骨架
  - 基于规则模板而非纯 LLM 生成，可控性更高
  - 前置条件：映射准确率 ≥ 95%
- [ ] **五层架构完整化**（借鉴 ClinAgent）
  - Agent → Skill Router → Skills → MCP Tools → Infrastructure
  - 支持多 Skill 编排（映射 + Spec 生成 + 代码生成 + 日志审查）
- [ ] **全局 KB 沉淀** — Session 级修正定期汇总审核后加入全局 KB，实现跨项目学习
- [ ] **端到端自动化** — 映射 → Spec → 代码 → 执行 → P21 验证

## 📋 更新日志

### v1.0 (2026-07-01)

**正式发布 — IG 3.4 兼容 & Prompt 优化 & 一致性校验增强 & 多 EDC 支持**

**Spec Mapper — IG 3.4 模板兼容**
- 新增 `config_ig34.yaml`，通过 `_extends: config.yaml` 继承基础配置，仅覆盖 IG 3.4 差异
- SpecMapper 自动读取模板 `CONTENT!B4` 版本号，3.4 → 自动选择 IG 3.4 配置
- 新增 `source_label_overrides`：F 列来源标签自动翻译（`指定` → `Assigned`，`衍生` → `Derived`）
- CONTENT E 列 Findings 类识别兼容中英文（`发现` / `Findings`）
- HYPERLINK 目标表单缺失时自动跳过（如 IG 3.4 无 VISIT 表，VISIT/VISITNUM 超链接静默跳过）
- 新增 `sdtmig_v3.4_variables.json`（63 域）作为标准变量权威数据源

**Prompt 优化**
- Prompt 内容从 Python 代码迁移到版本化 YAML 文件（`variable_mapping.yaml` v1.1.0 / `sdtm_rules.yaml` v1.0.0 / `pattern_examples.yaml` v1.0.0）
- 新增 6 步推理清单（reasoning checklist）：临床概念 → 域推断 → 标准/SUPP 判定 → TESTCD → 8 字符检查 → 表内一致性
- 新增 `sibling_variables_section`（同 CRF 表变量列表）和 `table_context_section`（已映射的同表结果），增强表内一致性
- 域变量展示优化：1-2 个候选域时展示全部标准变量，减少 LLM 发明非标准变量名
- 示例过滤收紧：仅展示匹配 `domain_hints` 的示例，减少干扰
- 新增 IG 3.4 域规则：OE、MK、GF、CP、XU、APMH 域关键词映射与 Domain 特定规则
- 新增 Prompt CI 验证脚本（`scripts/prompt_ci/validate_prompts.py`）：7 项静态检查（schema、占位符、规则 ID 唯一性、域合法性等）

**一致性校验增强**
- `MappingCritic` 从 5 项检查扩展至 **12 项**：
  - **新增** `supp_completeness`（error）：SUPP 记录必须有 `supp_dataset`、`supp_variable`（QNAM）、`sdtm_variable=QVAL`
  - **新增** `score_sanity`（error/warning）：分数超出 [0,1] 或个别分数 < 0.5
  - **新增** `kb_expression_integrity`（error）：KB 记录变量名截断后条件表达式完整性校验
  - **新增** `supp_naming_convention`（error）：SUPP QNAM 必须匹配 `^[A-Z0-9]{1,8}$`
  - **新增** `variable_coverage`（error）：每个输入 CRF 变量必须至少有 1 条有效推荐
  - **新增** `unmapped_ratio`（warning）：批次级 UNMAPPED 变量占比 > 10% 时预警
  - **新增** `low_confidence_ratio`（warning）：低置信度（< 0.7）变量占比 > 20% 时预警
- `DeterministicValidator` 新增 IG 3.4 标准变量校验：`sdtm_variable_type=standard` 的变量自动对照 IG 3.4 域变量列表，非标准变量标记 `non_standard_variable=True`

**多 EDC 系统支持**
- 新增太美 EDC 系统原始文件解析（`scripts/extract_taimei_sheet.py`），与百奥知 4.1 并列支持
- Web 界面 Step 1 新增 EDC 系统切换按钮（百奥知 4.1 / 太美）

**默认模型升级**
- 默认 LLM 模型从 `google/gemini-2.5-flash` 升级为 `google/gemini-3-flash-preview`
- Web 界面 LLM Model 下拉菜单调整为 Gemini 3 Flash Preview 为首选项

**架构升级**
- 新增 `src/processors/cascade.py`：级联预测逻辑从 `sdtm_processor.py` 提取为独立 `CascadePredictor`
- 新增 `src/models/boundary.py`：Pydantic 边界模型（`VariableInput`、`DomainRecommendationRecord`、`CascadeResult` 等），模块间接口运行时校验
- 新增 `src/processors/recommendation_orchestrator.py`：依赖注入式单变量处理管线编排
- `src/config/settings.py` 迁移至 pydantic-settings：分散的 `os.getenv` 统一收敛为类型化子模型（`AIProviderSettings`、`CascadeSettings`、`RAGSettings` 等），支持 `.env` 文件加载和单例访问

**User Guide 全面改版**
- 智能推荐流程章节重写：新增 4 级级联预测表格、后处理与验证（确定性规则 + 批量一致性校验）
- SDTM Spec 填充规则章节从 5 个子章节扩展至 9 个：
  - 通用规则 & 高亮（完整高亮颜色说明：黄/红/蓝/Sheet 标签）
  - CONTENT 表单 / CODELIST 表单 / Domain 表单（补充详细规则表格）
  - **新增** SUPP 变量处理、MedDRA & WHODRUG 编码变量、xxTEST 表单、IG 3.2 vs IG 3.4 差异对比

### v0.2.1 (2026-04-14)

**架构增强 — Phase 1 实施（借鉴 SATA & ClinAgent 论文）**

**级联预测策略**
- 实现 4 级级联退出机制（KB 精确 → KB 高置信度 → RAG 增强 → LLM 推理）
- Level 2/3 高置信度结果可直接采纳，跳过 LLM 调用
- 阈值通过环境变量可配置（`CASCADE_KB_HIGH_CONF`, `CASCADE_RAG_HIGH_CONF`）

**确定性规则引擎**
- 新增 `DeterministicValidator`：变量名超长时查找该域标准变量中最相似的合法变量名（而非简单截断）
  - 例：`AETERMDESC` (9字符) → `AETERM`，而非 `AETERMDE`
- 域合法性校验、变量-域前缀一致性检查
- 集成到 `PostprocessMixin` 后处理流程

**批量一致性校验**
- 新增 `MappingCritic`：对全批次映射结果做 5 项跨记录一致性检查
  - 同表域一致性、Findings TESTCD 关联、变量名合法性、域合法性、重复映射检测
- 集成到 `process_mappings()` 批处理末尾，错误/警告自动输出

**持续学习循环**
- 新增 `POST /api/corrections` 端点：接收用户修正，存储为 session 级别 parquet
- 修正记录自动去重（同变量保留最新修正）
- 下次映射运行自动加载修正记录作为 KB（confidence=1.0，最高优先级）
- `GET /api/corrections` 端点：查询当前 session 的修正记录

**GxP 合规基础设施**
- 新增 `AuditLogger`：结构化 JSONL 审计日志，记录每次映射操作
  - 记录级联层级、处理时间、验证问题
  - 支持 `log_mapping` / `log_batch_summary` / `log_correction` 三种操作类型
- 新增 `DataMasker`：PHI/PII 自动脱敏（SSN、受试者 ID、DOB、邮箱、电话）

### v0.2.0 (2026-03-12)

**架构重构 — Processor 模块化**
- 将 `sdtm_processor.py`（~3100 行）拆分为 4 个 Mixin + 编排层（~1550 行）
  - `CatalogMixin` — 标准目录加载与查询
  - `DomainInferenceMixin` — Domain 推断逻辑
  - `PostprocessMixin` — LLM 输出后处理与验证
  - `IOHelpersMixin` — 文件 I/O、进度保存、日志

**Prompt 优化**
- 移除与 `sdtm_rules` 重复的 Key Patterns 块
- 收紧示例域过滤：仅展示匹配 `domain_hints` 的示例
- 删除无用的 `build_catalog_section` 方法和 `PromptDebugger` 类
- 新增 `sdtm_variable` 纯变量名约束，防止 LLM 嵌入 "when" 子句

**跨模型鲁棒性**
- 新增 "when" 子句解析器：自动将 `"FAORRES when FATESTCD=THDIAG"` 拆解为 `sdtm_variable="FAORRES"` + `testcd="THDIAG"`
- 修复切换 LLM（如 gemini-2.5-flash → gemini-3-flash-preview）后映射结果全部变为 SUPP 的问题

**代码清理**
- 删除死方法：`_extract_domain_title`、`_load_json_mappings`、`_validate_domain_recs`
- 修复 `_infer_testcd` 中重复的 `'HR'` 键（改为 `'ECGHR'`）
- 修复 `process_mappings` 中的无效循环
- 移除所有 `PromptDebugger` 引用和注释掉的 RAG/KB 代码块

**Processor 去重**
- 新增 `_normalize_domain_recs` 去重：按 `(domain, sdtm_variable, testcd, supp_variable)` 合并重复推荐，保留最高分
- 新增 eCRF 合并前去重：按 `(metadata_table, metadata_variable)` 保留每个变量的最高分推荐，避免 merge 产生重复行

**Spec Mapper 增强**
- SUPP 变量长度：以数值写入并复制模板 `number_format`，保持 `$200` 格式一致
- SUPP 变量排序：按 CRF 原始行号 (`source_order`) 排序，不再按变量名字母序
- CODELIST 合并写入：已有 `(domain, testcd_var, testcd_value)` 行仅补填空白 I/J 列；新增行在该 domain 块末尾**插入**（`insert_rows`），避免覆盖模板下方内容
- CODELIST 新增 J 列 (EDC VAR)：填充 `metadata_variable` 值
- CONTENT F 列：ALS 来源域使用 `metadata_table` 值**替换**模板值（非追加），自动补充 `SDTM.DM` 和 `SDTM.SV`
- CONTENT SUPPxx 行：自动复制父域 F 列值
- Domain sheet D10：动态 `INDEX-MATCH` 公式引用 CONTENT 的 H 列排序变量
- 固定变量规则：VISIT/VISITNUM 支持 `exclude_domains` 配置（排除 TA/TD/TE/TI/TS/TV）
- 外部编码变量：长度以数值写入，格式与标准 SDTM 变量一致
- Excel 公式保护：含换行的 `=HYPERLINK(...)` 等混合内容自动剥离前导 `=`，防止公式移除错误

**项目结构**
- `app.py` 移至项目根目录，简化启动命令为 `uvicorn app:app`

### v0.1.1 (2026-01-05)

**Domain 推断优化**
- 🎯 优化 Domain 推断优先级：`annotation_table` 推断优先于 `metadata_table`
- ✅ 新增 Domain 验证：自动识别并纠正无效的 SDTM Domain（如 "CC"）
- 📝 扩展关键词映射：新增 肌酐清除率→LB、吸烟史→SU、饮酒史→SU 等
- 🔒 Prompt 约束强化：明确列出所有有效 SDTM Domain，防止 LLM 发明新 Domain

**Prompt 优化**
- 🚀 移除冗余的 `catalog_section`，统一使用动态 `domain_variables_section`
- 📊 传递预计算的 `domain_hints`，避免重复推断
- 🧹 移除 `variable_data['domain_hint']` 依赖，简化推断链

**代码清理**
- 🗑️ 移除 `_extract_domain_from_variable`（CRF 变量名不遵循 SDTM 命名）
- 🗑️ 移除短表名截取逻辑（如 "CCR" → "CC"）
- 📐 修复注释编号（# 5 → # 4）

### v0.1 (2025-12-31)

**功能**
- 🎉 Web 界面三步式工作流
- 🔐 Session 隔离：用户文件独立存储
- 📚 Session KB：用户上传的 KB 支持直接匹配和 RAG 检索
- 🛡️ 安全增强：路径遍历防护、文件上传限制、API 速率限制
- 🧹 自动清理：关闭浏览器自动清理文件，服务器定时清理过期目录

## 🔌 API 端点

### 映射与任务

| 方法 | 端点 | 说明 |
|------|------|------|
| `POST` | `/api/recommendations` | 启动 AI 映射推荐任务 |
| `GET` | `/api/jobs/{job_id}` | 查询任务状态 |
| `GET` | `/api/jobs/{job_id}/download` | 下载结果（Excel/JSON） |
| `POST` | `/api/jobs/{job_id}/cancel` | 取消任务 |

### 修正与学习

| 方法 | 端点 | 说明 |
|------|------|------|
| `POST` | `/api/corrections` | 提交映射修正（用户修正 → Session KB） |
| `GET` | `/api/corrections` | 查询当前 session 的修正记录 |

### Session 与文件

| 方法 | 端点 | 说明 |
|------|------|------|
| `POST` | `/api/session/init` | 初始化/恢复 session |
| `POST` | `/api/session/cleanup` | 清理 session 资源 |
| `POST` | `/api/upload` | 上传文件 |
| `GET` | `/api/files` | 列出已上传文件 |

### Spec 生成

| 方法 | 端点 | 说明 |
|------|------|------|
| `POST` | `/api/spec-mapper/run` | 运行 Spec Mapper |
| `POST` | `/api/convert-als2sdtm` | ALS2SDTM 转 parquet KB |
| `POST` | `/api/list-sheets` | 列出 Excel 工作表 |

所有端点需要 `X-Session-ID` 请求头标识用户会话。

---

**SIRIUS** - 让 SDTM 映射更智能、更高效
