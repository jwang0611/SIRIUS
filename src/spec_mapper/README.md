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

print(f"✓ Processed {stats['updates']} mappings")
print(f"✓ Inserted {stats['supp_records']} SUPP rows")
print(f"✓ Created {stats['conditional_records']} conditional sheets")
```

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
