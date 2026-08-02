# SIRIUS 产品深度评审与优化方案

> 评审日期：2026-07-12
> 评审对象：SIRIUS v1.0（main 分支当前状态）
> 评审方法：全代码库深度阅读（核心推荐管线 / Web 与前端 / Spec Mapper / 合规·测试·工程质量四条线并行分析），结合 README、CLAUDE.md、AGENTS.md、CI 配置与 git 历史交叉验证。所有发现均附带 `文件:行号` 证据。

---

## 0. Codex 复核结论与执行切片（2026-07-13）

本方案的优先级判断总体成立，尤其是“先度量与止血，再建设 QC 闭环”的顺序。复核同时确认：该 PR 原始提交仅增加评审文档，没有实现代码；A–D 横跨数月，不能作为一个原子变更一次落地。实施因此按可独立验收、且不改变 4 级级联语义的切片推进。

复核中对三处表述作如下校准：

1. OpenRouter 文本生成使用的 OpenAI Python SDK 本身带隐式重试，并非严格意义上的“零重试”；真实缺口是重试参数未显式配置、Embedding 的 `requests.post` 确为单次调用。本轮将两条路径统一为可配置的 429/5xx/超时重试。
2. `GET /session-stats` 只返回聚合计数，不暴露绝对路径；绝对路径泄露位于 `GET /session/{id}?detail=true`。本轮在 API 边界只返回文件名。
3. 类型化 settings 已定义 `google/gemini-3-flash-preview`，漂移来自生产调用绕过 settings。本轮让客户端、后台任务和 API 缺省值统一回到 settings，而不是再新增一份常量。

### 本 PR 的实施批次

| 项目 | 本轮动作 | 验收 |
|---|---|---|
| A2 外部调用重试 | 文本生成显式配置 SDK retry/timeout；Embedding 对 429、5xx、连接/读取失败做指数退避 | 单测覆盖 429 后成功与连续失败 |
| A3 静默失败止血 | 统计 `FALLBACK/*_PENDING` 与 MappingCritic error；任务返回 `completed_with_errors`，前端醒目提示但仍允许下载复核 | API/任务/UI 测试覆盖 |
| A4 Spec 假进度移除 | 删除定时递增线程，`SpecMapper.process` 按读取 ALS、读取模板、映射、写入、保存回调真实阶段 | 回调顺序测试覆盖 |
| A6 文档与默认值 | 默认模型回归 typed settings；README 降级多用户/GxP 声明并指向本方案 | 设置与 API 测试覆盖 |
| B7/速赢安全项 | Toast 改安全 DOM 构造；限流不信任 Session header；隐藏服务器绝对路径；本地绑定、CORS 与 reload 默认收紧；增加健康/版本端点 | Web 安全与端点测试覆盖 |
| 敏感日志默认值 | 完整 prompt/response 与 KB 交互落盘改为显式 opt-in | settings 测试覆盖 |

### 后续里程碑与前置条件

- A1 必须由维护者提供至少两个与 KB/关键词表不相交的真实、已去标识化研究作为 held-out 数据；不得用仓库现有 KB 再造“测试集”。
- A5 的“实际写入数”需要 ExcelWriter 各写操作返回结构化结果，本轮只先让任务失败/告警可见；建议与真实模板端到端测试一起实施。
- A7 已在独立 PR 中选择 uv：runtime/dev/build 分组、通用锁文件、带哈希 pip 导出、两次全新 Python 3.11 安装校验、mypy 与 coverage 门禁一并交付；内部环境仍需使用运维提供的版本化镜像 URL 执行同一校验。
- Phase B–D 保持原路线顺序：QC 工作台 → 产品形态/身份与审计 → 模板抽象与价值延伸。进入下一阶段前，以本节验收项和 A1 无泄漏评测为闸门。

---

## 1. 执行摘要

SIRIUS 的产品承诺非常清晰且正确：**不是"LLM 替你决定映射"，而是"给临床编程与标准评审人员一个可评审、可审计的智能助手，每一条 AI 建议外面都包着确定性校验"**。工程底子是好的：4 级级联架构真实存在且有测试、Spec Mapper 对工作簿格式的保真处理很用心、约 590 个单测 + 特征化/快照测试、CI 已建立、SSRF/路径遍历等安全细节做得比多数内部工具认真。

但对照产品承诺逐条检验，当前版本存在 **四个结构性缺口**，它们不是代码 bug，而是产品层面的断裂：

| # | 结构性缺口 | 一句话概括 |
|---|-----------|-----------|
| 1 | **价值闭环断裂** | 产品最核心的环节——人对 AI 推荐的 QC 评审——完全发生在产品之外（下载 Excel → 线下改 → 重新上传），在线评审 UI 在 v0.2.1 被移除后没有替代品，corrections API 成为前端零调用的孤儿接口 |
| 2 | **准确率不可度量** | 唯一的评测脚本存在训练/测试数据泄漏，置信度分数来源混杂且未校准；既无法向客户证明"越用越准"，也无法安全地迭代 prompt/阈值/模型 |
| 3 | **合规叙事与实现差距** | 以"GxP 审计日志"为卖点，但系统里没有任何用户身份——审计记录只关联匿名 session，无防篡改机制、无电子签名、无依赖冻结，距 21 CFR Part 11 的核心要求尚远 |
| 4 | **产品形态摇摆** | README 宣称"多用户并发"，但任务/会话/限流全部是单进程内存态，重启即失、无法多 worker、跨用户文件互相可见可删——它今天实际上是一个单机桌面助手，却背着多用户服务的包袱 |

在此之下还有一层可靠性/成本问题（外部调用零重试、失败静默降级为 PENDING 占位符、每变量一次 LLM 调用无批处理）和一层工程债（生产/DI 双引擎并存漂移、settings 双配置源、README 引用不存在的文档）。

**建议的总路线**：先花 2~4 周建立"度量地基 + 止血"（评测集去泄漏并入 CI、外部调用重试、消灭静默失败），再用 1~3 个月把 QC 评审工作台做回产品里（这是唯一能形成数据飞轮的入口），合规与企业化（身份、防篡改审计、部署形态）作为第三阶段按目标市场决定投入深度。**在准确率可信度量建立之前，不建议投入 Phase 4 的代码生成等新方向。**

---

## 2. 产品现状评估

### 2.1 值得保留的资产（评审中确认的优点）

- **级联架构与确定性护栏的设计哲学正确**：KB 精确 → KB 高置信 → RAG → LLM，配合 `DeterministicValidator` / `MappingCritic` / normalizer 的后置校验，这个"AI 外面包确定性"的骨架是产品护城河，应坚持。
- **Spec Mapper 的工作簿保真**：复制模板再编辑、缺 sheet 优雅降级（VISIT 超链接自动跳过有专门测试）、`config_ig34.yaml` 用 `_extends` 干净继承、缺列时报出可读的 `ValueError`（`src/spec_mapper/core/excel_reader.py:119-123`）。
- **测试文化真实存在**：~590 个测试、真 fake（`FakeAIClient`/`InMemoryKB`，`tests/conftest.py:79-237`）、golden/快照测试、prompt CI 静态检查 7 项；单测不依赖线上凭据。
- **安全细节高于平均水准**：LLM 自定义 endpoint 的 SSRF 防护完整（scheme 校验、拒绝云元数据地址、主机白名单，`src/web/security.py:420-446`）；非默认 endpoint 强制自带 token、服务器密钥不外发（`src/web/routers/jobs.py:55-60`）；路径遍历、session 前缀碰撞有回归测试。
- **诚实的文档姿态**：README 明确承认 PHI 脱敏的本地化空白（`README.md:359`）、明确说明在线编辑已移除——这种诚实是合规产品的正确姿态，应延续。
- **桌面端封装用心**：Electron 外壳的端口探测、崩溃竞速检测、单实例锁、Windows 进程树清理（`desktop/main.js`）。

### 2.2 产品承诺 vs 现实差距矩阵

| 产品承诺（README/CLAUDE.md） | 现实 | 证据 |
|---|---|---|
| "可评审的助手"（reviewable） | 评审全程发生在 Excel 里，产品内无任何查看/编辑/筛选推荐结果的界面；结果页只有两个下载按钮 | `src/web/static/app.js:948-959`；`README.md:334`（"目前无 WebUI 包装"） |
| "持续学习循环" | `/api/corrections` 前端零调用（孤儿接口）；主路径依赖用户在 Step 3 重新上传 QC 后文件 | `src/web/routers/corrections.py` vs `app.js`（仅 i18n 字符串提及） |
| "GxP 审计日志" | 审计仅关联匿名 session、无用户身份、无防篡改、写失败仅 warning 不阻断 | `src/infrastructure/audit_logger.py:84-85, 218-219` |
| "减少 40-60% LLM 调用" | 无任何度量产物支撑；唯一评测脚本对着已进 KB 的同一份数据测 | `README.md:459`；`scripts/eval_prompt_accuracy.py:46` |
| "支持多用户并发" | 任务/会话/限流全为单进程内存态；多 worker 部署即坏；跨用户文件列表/删除无属主校验 | `src/web/job_manager.py:42-47,105`；`src/web/routers/files.py:33-48,70-82` |
| "响应式前端" | ≤980px 侧边栏被 `display:none` 且汉堡键失效，移动端实际无法切换页面 | `src/web/static/styles.css:299-303`；`app.js:385` |
| "LLM 调用前 PHI/PII 脱敏" | 仅遮罩 variable_data 四个字段；同表兄弟变量注释、KB 建议、RAG 片段以未脱敏形式进入 prompt；中文标识符完全未覆盖 | `src/processors/sdtm_processor.py:1623`；`src/infrastructure/data_masker.py:27-41,78` |
| "完整开发规格见 docs/OPTIMIZATION_SPEC.md" | `docs/` 整个目录被 .gitignore 忽略，该文件在仓库中不存在；`.gitlab-ci.yml` 引用的 `docs/CICD_GUIDE.md` 同样不存在 | `.gitignore:81-82`；`README.md:455` |
| 默认模型 `gemini-3-flash-preview` | 运行时兜底默认仍是 `gemini-2.5-flash` | `src/clients/openrouter_client.py:28`；`src/web/tasks.py:173` |

---

## 3. 核心发现详述

### 发现一（最高优先）：价值闭环断裂 —— QC 评审脱离产品

**现状**：Step 2 完成后用户只能下载 Excel/JSON（`app.js:948-959`）；置信度颜色、来源标签、一致性问题（Consistency_Issues sheet）全部只存在于下载的 Excel 中，浏览器内一概不可见。用户线下 QC 后在 Step 3 重新上传，靠 `project_name` 归档回 session KB（`src/web/routers/spec_mapper.py:67-82`）。同时：

- `/api/corrections`（POST/GET）实现完整但前端从未调用——"持续学习循环"的细粒度入口废置；
- "Knowledge Base" 导航页是纯静态占位（KB 文档数显示 "—"，i18n key 自己承认 `'Backend stats not yet wired'`，`app.js:129`；`index.html:84-87,408-411`）；
- session 关闭 3 秒后清理所有生成文件（`src/web/session_manager.py:32,216`），用户指南只能提醒"请及时下载"。

**为什么这是第一优先**：这个产品的差异化价值不在"生成一版映射"（任何人拿 LLM 都能做），而在**评审效率 + 修正沉淀**。QC 在产品外发生意味着：(a) 用户 80% 的工作时间产品不在场，无粘性；(b) 修正数据无法结构化回流，"越用越准"的飞轮只能靠用户自觉重新上传整个文件；(c) 无法度量"AI 推荐被人改了多少"——而这恰恰是产品最重要的北极星指标。

### 发现二：准确率不可度量 —— 产品价值无法证明，迭代无安全网

- **评测泄漏（高危）**：`scripts/eval_prompt_accuracy.py:46` 用 `ALS2SDTM_TEST.json` 做 ground truth，但同一份数据 (a) 是硬编码域映射表的来源（`src/config/domain_semantic_map.py:205` 注释自认 "Extended from ALS2SDTM_TEST.json"），(b) 通常又被配置为默认 KB，产生 confidence=1.0 的精确匹配（`src/knowledge_base/llm_query_interface.py:704`）。等于用训练集考试，数字必然虚高，且完全测不出泛化能力。
- **置信度未校准（高危）**：KB 精确=1.0、归一化精确=0.95、模糊=0.80+score×0.12、**向量匹配无论真实余弦多少一律硬编码 0.95**（`llm_query_interface.py:994`）、LLM 层直接透传模型自报分数（`sdtm_processor.py:979`）。这些量纲不同的分数被同一套阈值（级联退出）和同一套 Excel 红黄绿着色消费——"绿色=可信"的暗示没有统计基础。
- **阈值全部是拍脑袋值**：`0.85/0.70` 级联阈值、RAG 用原始 embedding 余弦对比 0.70（Qwen embedding 相似度地板高，0.70 证据薄弱）、`_RAG_DOMAIN_MISMATCH_PENALTY=0.5`（`sdtm_processor.py:1764`）——没有任何针对实测准确率的调参痕迹。
- **评测未入 CI**：prompt/模型/KB 任何改动都可能静默劣化映射质量而无人察觉。
- 附带问题：硬编码的 ~340 条中文表名映射混入了大量单研究特例（"宫颈癌病史"→FA、"7点-SMBG（糖尿病）"→LB 等，`domain_semantic_map.py:299-336`），子串首中即返回（`:1058`），跨研究会静默错路由。

### 发现三："GxP 合规"叙事与实现差距

对照 21 CFR Part 11 核心条款：

| 要求 | 状态 | 证据 |
|---|---|---|
| §11.10(d)(g) 唯一用户身份 + 权限 | **缺失**：全系统无任何认证；唯一"身份"是客户端自生成的 `X-Session-ID` | `app.js:418-427`；`corrections.py:137,145` |
| §11.10(e) 防篡改审计追踪 | **部分**：UTC 时间戳有；但纯文件追加、无哈希链/序号/签名，审计写失败仅 `logger.warning` 继续执行 | `audit_logger.py:210-219` |
| 电子签名 | **缺失**：修正无签名意图、无复核人、无记录锁定 | `corrections.py:132-147` |
| 验证文档（IQ/OQ/PQ、追溯矩阵） | **缺失**：docs/ 被 gitignore | `.gitignore:81-82` |
| 可重现构建 | **A7 已实现，待合并**：`pyproject.toml` 分组 + `uv.lock` + 带哈希 pip 导出；源地址由环境注入，不再提交 `/latest/` | `pyproject.toml`、`uv.lock`、ADR 0001 |

另外两个合规相关实质问题：

- **审计记录缺少复现要素**：不记录 model id、prompt 版本、KB 版本、应用版本（`audit_logger.py:82-103`）——AI 辅助决策无法追溯到产生它的确切模型与提示词。
- **脱敏覆盖与声明不符**：生产路径只对 variable_data 四字段脱敏（`sdtm_processor.py:1623`；`data_masker.py:78`），兄弟变量、KB 建议、RAG 片段绕过脱敏直接进 prompt；整段 prompt 脱敏的 `LLMInferenceService.mask_text` 路径存在但未接入生产（见发现八）。中文身份证/手机号/姓名零覆盖且无 CJK 测试。同时 `env_template.txt:135` 默认 `SDTM_LOG_AI=true` 保存完整 prompt+response——与 CLAUDE.md "不做原始 prompt 日志" 的锁定决策相抵触。

### 发现四：产品形态摇摆 —— 单机助手 vs 多用户服务

- 任务=裸 daemon 线程 + 内存字典（`tasks.py:397-412`；`job_manager.py:42-47`）；服务器重启所有任务/会话消失，前端轮询得到 404；spec 任务无断点恢复。
- `start.sh` 默认 `RELOAD=1`——开发特性作为默认运行模式，文件变更即杀掉进行中任务。
- **多 worker 部署根本不可行**（任务/会话/限流全在进程内存），而 README 宣称多用户并发。
- **跨用户隔离失效**：文件列表全局 glob（`files.py:33-48`）、任何 session 可对 `data/processed` 里任何文件发起推荐任务（`jobs.py:70-74`）、可删除任何人的输出（`files.py:70-82`）、同名上传静默互相覆盖。
- 限流 key 优先取客户端自报的 `X-Session-ID`（`security.py:31-34`），换个 header 即绕过。
- CORS 全开 `allow_origins=["*"]`（`app.py:39-44`）+ 无认证 + 默认绑 `0.0.0.0`：局域网内任何人、用户浏览器里任何网页都能驱动全部 API。
- 前端 `showToast` 用 `innerHTML` 注入含上传文件名的消息（`app.js:547-554,692`）→ 文件名可携带 XSS；LLM API token 存 `localStorage`（`app.js:268-270`）→ 与 XSS 组合成现实的密钥窃取路径。

**产品判断**：这不是"补几个漏洞"的问题，而是要**先做形态决策**：近期（当前用户=院内/公司内少数临床编程人员）应明确定位为**单用户桌面/单机工具**（Electron 路线正好匹配），把"多用户并发"从 README 撤下，绑定 `127.0.0.1`、收紧 CORS；企业服务器版（认证 + 持久化 + 队列）作为独立里程碑立项，不要让两种形态的假设混在同一套内存态代码里。

### 发现五：可靠性 —— 零重试 + 静默失败

- **重试行为不一致且不可审计**：OpenRouter 文本生成依赖 OpenAI SDK 的隐式默认重试，代码中没有显式参数；embeddings 是单次 `requests.post`、失败即抛（`src/rag/embeddings.py:61`）。
- 单变量 LLM 失败被降级为 `score=0.0`、`sdtm_variable="{DOMAIN}_{VAR}_PENDING"` 的占位记录（`sdtm_processor.py:1018-1034`）。一次 429 限流风暴可让大批变量静默变成垃圾占位符，而任务显示"完成"。
- 混合模式下整表失败仅日志后跳过（`sdtm_processor.py:1424-1427`）；批次永远"成功"，唯一信号是 MappingCritic 的建议性告警——它从不阻断、不重跑、不改变任务状态（`:2182-2195`）。
- Spec Mapper 同样是静默失败模型：写入阶段 11 处 `try/except Exception` 只 warning 继续（`src/spec_mapper/__init__.py:332-467`），可能丢 SUPP 行/CODELIST/整域仍返回"成功"stats——且 stats 报告的是**计划数**不是**实际写入数**。
- Spec 任务的进度条是**假的**：后台线程每 0.5 秒 +5% 直到 90%（`tasks.py:493-516` 有注释自认），与真实工作无关。生产 UI 不应报告模拟状态——这触碰了 CLAUDE.md 锁定决策第 5 条的边界。

### 发现六：成本与性能

- **每变量一次 LLM 调用，无批处理**（`sdtm_processor.py:889`）：300 变量的 CRF 若大量落到 Level 4，就是 300 次完整 round-trip，每次重发整套规则/域变量/示例/兄弟上下文。同表变量本已共享上下文，**按表打包一次结构化调用是最明显的 5-10 倍成本/延迟杠杆**。
- KB 被两条独立管线各自加载+embedding 一次（`llm_query_interface.py` 与 `prompt_augmenter.py` 各有缓存体系）；每个 `/api/recommendations` 请求重建整个 `SDTMProcessor` 并重载 KB（`tasks.py:271`）。
- KB 向量检索每次查询对全库矩阵重新归一化（`llm_query_interface.py:557-566`）。
- 域推断同一变量被重复计算 3-4 次（`sdtm_processor.py:1546,827,1630`）。
- 取消是协作式的，进行中的 LLM 调用无法中断；spec 任务 UI 甚至没有取消按钮。

### 发现七：Spec Mapper——产品最贵的资产建立在最脆的地基上

- **写入端整体硬编码列索引/行号**：读取端是表头驱动的（好），但 `excel_writer.py` 所有后处理写死 A=1…K=11、数据起始行 14、CONTENT 格式参考行 52、`A15="DOMAIN"` 等魔法坐标（`excel_writer.py:674-677,1007-1011,1503-1516,482,1140`）。换任何一家 sponsor 的模板即全线崩塌——**当前"支持模板"实际上是"焊死在两个 CDISC-CN 模板上"**。
- 仅 2 字符纯字母 sheet 被识别为域（`config.yaml`；`__init__.py:340`），拆分数据集/自定义域被静默跳过。
- 版本探测只看 `CONTENT!B4` 且 `startswith("3.4")` else 3.2 —— IG 3.3 或厂商变体静默套错配置（`__init__.py:166-194`）。
- **写入路径实质零测试**：唯一的集成触点是 `dry_run=True` 的 smoke（`tests/smoke_test.py:645`；`__init__.py:278-281` dry_run 在写入前就返回），1,614 行 excel_writer 的 SUPP 插入/CODELIST 插入移位/超链接修复/公式生成全部无保护。DSL 解析器的专属行为测试已在 Issue #12 一致性清理中补齐；Excel 写入端到端保护仍属于 A5。
- 插入行会**摧毁**重叠的合并单元格且不恢复（`format_utils.py:63-77`）；CONTENT SUPP 行重跑可能重复追加。
- ALS sheet 默认名差异已在 Issue #12 一致性清理中解决：运行时、CLI 与文档统一为 `Sheet1`，并用行为测试锁定“显式参数 > 环境变量 > 配置”的优先级。
- EDC 提取脚本（百奥知/太美）~90% 代码互相复制，厂商差异以模块常量硬编码，无适配器抽象——新增一家 EDC = 重写 300 行脚本。

### 发现八：工程债 —— 双引擎、双配置、文档漂移

- **两套管线并存且文档指错方向（高危）**：`CascadePredictor` / `RecommendationOrchestrator` / `RecommendationNormalizer` / `LLMInferenceService` 这套干净的 DI 管线**只被测试引用**，生产 `SDTMProcessor` 用的是自己内联的 `_try_cascade_shortcircuit`（`sdtm_processor.py:575`）和 `PostprocessMixin` 的重复实现。`normalizer.py:6` 的 docstring 声称"mixin 已委托给它们"（不实），CLAUDE.md 也把 DI 管线描述为现役。级联决策、SUPP/when 子句、去重逻辑各有两份拷贝要人肉同步——**必须二选一：接入或删除**。
- 旧 `_process_mappings_parallel` 及其专用单变量 helper 已在 Issue #12 一致性清理中删除；生产保留表间并行、表内顺序处理的 hybrid 路径。
- **双配置源**：pydantic-settings 的类型化配置树只被测试/日志使用，生产代码仍满地 `os.getenv`（`sdtm_processor.py:316-480` 十余处；`tasks.py:27-31`），且两边默认值已经打架（默认模型不一致即为一例）。
- 验证逻辑三处重叠：`DeterministicValidator`、`_compute_ig34_check`（`sdtm_processor.py:86`）、`MappingCritic` 各自实现 QNAM/变量合法性判断。
- A7 已把 mypy 作为 GitHub CI 阻断项，并分离 runtime/dev/build 依赖、提交精确锁文件与 coverage artifact；类型严格范围仍应后续逐步扩大。`app.py` 用已废弃的 `@app.on_event("startup")`；集中式 logger 建好了但 `session_manager.py`/`app.py` 仍大量 `print()`（中文、无结构，无法聚合监控）。
- Excel 主交付物的合并去重会把合法的多映射（标准变量 + SUPP 限定符）折叠成单行（`io_helpers.py:196-208`），JSON 保留但 Excel 丢失——需要与交付契约确认是否符合预期。

---

## 4. 优化方向与分阶段方案

### 4.0 先定义北极星指标（一切优化的前提）

建议以三个指标作为产品度量体系，所有 Phase 的验收都挂在它们上面：

1. **QC 修改率**：AI 推荐在人工评审中被修改的比例（按变量计）。目标方向：持续下降。这是"越用越准"的唯一诚实证据。
2. **每研究映射交付时间**：从上传 ALS 到 QC 完成、Spec 生成的端到端时长。
3. **每研究 LLM 成本**：Level 4 命中率 × 单次调用成本。README 的"减少 40-60% 调用"应改由此指标实测替代。

当前这三个指标一个都测不了——修改率因 QC 在产品外无从统计，成本无埋点，时间无任务历史。这就是为什么度量地基排在第一阶段。

---

### Phase A（0-4 周）：止血与度量地基

**目标：让"改动是否让产品变好"变成可回答的问题；消灭最危险的静默失败。**

> **状态更新（2026-08-01）**：PR #11 已完成 A2、A3、A4、A6；A5 已提交独立 Draft PR；A7 已在独立分支实现并进入验证。A1 仍等待至少两个获准、去标识化且与 KB/关键词来源不相交的 held-out metadata 数据集，因此 Phase B 准入闸门仍未开放。

| # | 事项 | 关键动作 | 验收标准 |
|---|------|---------|---------|
| A1 | 评测集去泄漏 + 入 CI | 构建与 KB/关键词表严格不相交的 held-out 评测集（≥2 个研究来源）；`eval_prompt_accuracy.py` 改造为可离线跑（fake client 回放固定 LLM 输出）+ 可选真实 LLM 模式；CI 加准确率回归门禁 | CI 中任何 prompt/阈值/KB 改动触发评测；报告 domain 准确率、variable 准确率、SUPP 判定准确率三个数 |
| A2 | 外部调用重试 | OpenRouter/embeddings 加指数退避重试（429/5xx/超时，3 次），区分可重试与不可重试错误类型 | 单元测试覆盖 429→重试→成功、连续失败→明确报错两条路径 |
| A3 | 消灭静默失败 | LLM 失败的 `*_PENDING` 占位记录必须计数并反映到任务状态（如 `completed_with_errors`）与 Excel 醒目标记；整表失败向用户报告；MappingCritic 的 error 级问题计入任务结论 | 任务结果包含 `failed_variables` 计数；前端展示；不存在"看起来完整实际带占位符"的输出 |
| A4 | Spec 假进度条移除 | `tasks.py:493-516` 模拟进度删除，改为阶段性真实进度（读取/映射/写入/保存 4 个真实阶段），SpecMapper.process 增加 progress callback | UI 显示的阶段与日志一致；符合 CLAUDE.md 锁定决策第 5 条 |
| A5 | Spec 写入统计改为实际数 | `__init__.py` 的 11 处吞异常块改为收集 `warnings` 清单随结果返回；stats 报实际写入数 | 每次 spec 生成产出"跳过/失败项清单"，前端可见 |
| A6 | 修正文档漂移 | 撤下或兑现 `docs/OPTIMIZATION_SPEC.md` 引用；README "多用户并发"改为如实描述；默认模型统一为一处定义（settings）；`normalizer.py`/CLAUDE.md 关于 DI 管线的不实描述更正 | README/CLAUDE.md 与代码行为一致 |
| A7 | 依赖冻结 | 生成 lockfile（pip-tools/uv），运行时与开发依赖分离，内部源脱离 `/latest/` 通道 | 两次全新安装产出相同版本集 |

### Phase B（1-3 月）：把 QC 评审做回产品 —— 审阅工作台

**目标：用户 QC 工作在产品内完成，修正结构化沉淀，北极星指标 1 可统计。这是本方案中商业价值最高的一项。**

| # | 事项 | 关键动作 | 验收标准 |
|---|------|---------|---------|
| B1 | 推荐结果审阅表格 | Step 2 完成后在浏览器内呈现结果表：按表分组、置信度红黄绿、来源(KB/RAG/LLM/correction/project)标签、Critic 告警行内标记、低置信度/告警快速筛选 | 用户不下载 Excel 即可完成浏览与筛选；Excel 下载保留 |
| B2 | 行内修正 → corrections API | 每行可改 Domain/Variable/SUPP 字段，保存即调用现有 `/api/corrections`（孤儿接口复活），批准/驳回状态留痕 | 修正即时进 session KB；重跑同变量命中 correction 来源；QC 修改率自动统计 |
| B3 | 任务持久化 + 重连 | 任务元数据落盘（SQLite 即可）；刷新页面/重开 tab 可列出并重连进行中/历史任务；spec 任务加断点或至少幂等重跑 | 服务重启后任务历史可见；刷新不再丢任务 |
| B4 | 会话数据保护 | 生成物默认保留（项目目录），"关闭即删"改为显式清理动作 + 保留期策略；修复 8h 孤儿清理误删活跃工作的竞态（`session_manager.py:297-338`） | 用户关浏览器不再丢失未下载成果 |
| B5 | KB 管理页去占位 | Library 页接真实后端：默认 KB/项目 KB/correction 条目计数、来源分层展示、条目查看与删除 | `'Backend stats not yet wired'` 类占位全部消失 |
| B6 | 按表批量 LLM 调用 | Level 4 同表变量打包为一次结构化调用（共享规则/域上下文），配合 A1 评测验证准确率不降 | LLM 调用次数下降（北极星指标 3）；评测分数不回退 |
| B7 | 前端基础加固 | `innerHTML` 注入点改 textContent/转义（文件名 XSS）；动态提示 i18n 补全（含 "Duration"/"用时" 匹配 bug，`app.js:943-945`）；移动端导航修复或明确声明桌面专用 | 安全扫描无 DOM XSS;中英文切换后无残留中文提示 |

### Phase C（3-6 月）：形态决断与合规就绪

**先做产品形态决策，再按所选形态投入，避免两头下注：**

- **路线 C-单机（推荐先行）**：明确当前版本为单用户桌面产品。绑定 127.0.0.1、收紧 CORS、README 撤下多用户承诺；Electron 打包签名；本地项目文件持久化。成本低，与现有代码假设一致。
- **路线 C-企业版（按商业需求立项）**：认证（对接企业 SSO/LDAP）、任务队列与持久化后端、多 worker、按用户隔离存储、TLS 反代部署文档 + Docker 镜像、`/healthz` `/version` 端点。

无论哪条路线，**合规主张要与实现对齐**（否则从 README 撤下 GxP 字样）：

| # | 事项 | 关键动作 |
|---|------|---------|
| C1 | 用户身份进审计 | 最低限度：启动时/登录时获取操作者身份，`corrected_by`、审计条目、Spec 产物元数据全部记录真实用户 |
| C2 | 审计防篡改 | 条目哈希链 + 序号 + 记录 schema 版本；审计写失败升级为阻断性错误；审计文件轮转与保留策略 |
| C3 | 决策可复现 | 审计条目补记 model id、prompt YAML 版本、KB 签名、应用版本 |
| C4 | 电子签名工作流 | 修正/最终 Spec 的复核-批准两级签名（若目标客户确需 Part 11） |
| C5 | 脱敏本地化 | 中国身份证/手机号/中文姓名模式 + CJK 测试集；prompt 全文脱敏路径接入生产（兄弟变量/KB/RAG 片段过 mask_text）；`SDTM_LOG_AI` 默认关闭或仅存脱敏后内容 |
| C6 | 验证包 | IQ/OQ/PQ 模板、需求-测试追溯矩阵（现有 590 测试是很好的底子），docs/ 移出 gitignore 纳入版本控制 |

### Phase D（6 月+）：价值延伸（以 A1 指标达标为闸门）

按投入产出排序：

1. **Spec Mapper 模板抽象层**：列坐标/起始行/魔法行全部下沉到模板描述配置（每个模板家族一个 YAML），写入路径补齐端到端测试（真实模板 + 断言产物）——这是接第二家 sponsor 模板的前提，也是当前最大的商业扩展瓶颈。
2. **EDC 适配器框架**：抽出共享 IO/CLI 基座，每家 EDC 收敛为声明式适配器（sheet 名 + 列映射 + 过滤 + 可选 merge 钩子），把"新增一家 EDC"从重写脚本变成写配置。
3. **Spec 版本化与 diff**：产物内嵌 provenance（源 ALS 哈希、配置版本、工具版本），提供两版 Spec 的差异对比——契合用户"高亮评审"的既有心智。
4. **define.xml 导出 + P21/CT 一致性检查**：SDTM 交付物的自然下一站，显著提升"端到端"叙事。
5. **代码生成（原 Phase 4）**：维持原定前置条件（映射准确率 ≥95%），且"准确率"必须以 A1 的无泄漏评测为准。
6. **双引擎收敛**：将 `SDTMProcessor` 迁移到 DI 管线（CascadePredictor/Orchestrator）或删除后者；`os.getenv` 全面收敛到 settings——建议穿插在 B/C 阶段的顺手重构中完成，不单独立项。旧 `_process_mappings_parallel` 已删除。

---

## 5. 两周内可完成的速赢清单

1. 统一默认模型定义（`openrouter_client.py:28`、`tasks.py:173` → settings 单点）。
2. `showToast`/结果区 `innerHTML` → 安全插值（XSS 止血，半天）。
3. 限流 key 不再信任客户端 `X-Session-ID`，回退 IP（`security.py:31-34`）。
4. `GET /session/{id}?detail=true` 移除绝对路径泄露并加最小防护；`GET /session-stats` 仅保留聚合计数（`session.py:60-72`）。
5. 5xx 错误响应停止透传 `str(exc)`/完整命令行（`upload.py:111`；`security.py:337-347`）。
6. 加 `/healthz` + `/version` 端点（desktop 外壳与 CI 探活都在等它）。
7. GitHub CI 补 mypy（先 non-blocking 再转 blocking）与 coverage 报告。
8. `start.sh` 默认 `RELOAD=0`，reload 仅开发文档提及。
9. [x] ALS sheet 默认名与 README 对齐（统一为 `Sheet1`，并锁定参数/环境变量/配置优先级）。
10. 审计写失败从 warning 升级为可配置的阻断（`audit_logger.py:218`）。
11. [x] 死代码 `_process_mappings_parallel` 及其专用 helper 删除。
12. [x] `sdtm_parser.py` 增加专属 DSL 单测，覆盖 `when`、`if`、`|`、`/`、`//`、SUPP 与 assignment。

---

## 6. 风险与不建议做的事

- **不建议**在 A1（无泄漏评测）落地前调整任何级联阈值、prompt 或更换默认模型——没有安全网的调参是负期望行为。
- **不建议**现在启动 Phase 4 代码生成或"五层架构完整化"——在 QC 闭环与度量缺位时加新层只会放大不可观测性。
- **不建议**为了"多用户"仓促加一个简单密码层——半吊子认证会给合规审计留下更差的印象；形态决策（C 阶段）先行。
- **注意**：README 中 GxP/合规措辞在 C1-C3 完成前建议降级为"面向 GxP 的设计方向（audit-ready design）"，避免在客户审计中形成与实现不符的书面承诺——这对一家药企背景的产品尤其重要。

---

## 附录：各模块发现严重度速查

| 模块 | 高危 | 中危（代表） |
|---|---|---|
| 推荐管线 | 评测泄漏；置信度未校准；零重试；每变量一次 LLM 调用；双引擎漂移 | 阈值无调参依据；域推断重复计算；MappingCritic 仅建议性；LLM 输出解析用脆弱的字符串切分（生产路径未用更稳的 `LLMInferenceService.parse_response`） |
| Web/前端 | QC 无界面；任务/会话内存态；跨用户文件可见可删；无认证 | 假 spec 进度条；corrections 孤儿接口；KB 页占位；限流可绕过；XSS+localStorage token；移动端导航失效；i18n 半成品 |
| Spec Mapper | 写入端列索引硬编码；静默失败模型；写入路径零端到端测试；仅支持两个模板家族 | 合并单元格破坏；魔法行 52/A15；god-module（mapper 1301 行/writer 1614 行）；EDC 脚本复制粘贴 |
| 合规/工程 | 审计无用户身份；无防篡改；依赖未冻结；脱敏本地化缺失且上下文绕过 | 审计缺模型/prompt/KB 版本；无验证包；mypy 非阻断；无健康检查/容器化;审计日志无轮转无备份 |

*本报告全部结论基于当前分支代码的静态深度阅读；行号以评审当日代码为准。*
