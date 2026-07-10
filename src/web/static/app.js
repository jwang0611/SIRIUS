// ==================== SIRIUS Web App ====================
// SDTM Intelligent Recommendation & Inference Unified System
// Sidebar navigation · zh/en i18n · warm/light/dark themes.

// ==================== i18n ====================
const I18N = {
  zh: {
    'sidebar.system': '智能推理统一系统',
    'nav.dashboard': '工作台', 'nav.dashboard.desc': '任务总览与快速操作',
    'nav.upload': '上传预处理', 'nav.upload.desc': 'ALS 文件导入与转换',
    'nav.recommend': '智能推荐', 'nav.recommend.desc': '4 级级联推荐与推理',
    'nav.specgen': '生成 Spec', 'nav.specgen.desc': 'SDTM 规范文档输出',
    'nav.library': '知识库', 'nav.library.desc': '标准 / 模板 / 历史映射',
    'nav.guide': '使用指南', 'nav.guide.desc': '系统帮助文档',

    'dash.title': 'SDTM 智能推理工作台',
    'dash.lead': 'SIRIUS — SDTM 智能推荐与推理统一系统。基于 RAG 级联策略，从 CRF 变量到 SDTM 规范的端到端智能映射。',
    'dash.btn.guide': '使用指南', 'dash.btn.new': '开始新任务',
    'kpi.kb': '知识库文档', 'kpi.kb.sub': '已索引示例映射',
    'kpi.acc': '推荐准确率', 'kpi.acc.sub': '尚未接入后端统计',
    'kpi.domains': '支持域', 'kpi.cascade': '级联层级', 'kpi.cascade.sub': 'KB → RAG → LLM',
    'pipe.upload': '上传预处理', 'pipe.upload.desc': 'ALS 文件转 JSON',
    'pipe.recommend': '智能推荐', 'pipe.recommend.desc': '级联推荐与推理',
    'pipe.specgen': '生成 Spec', 'pipe.specgen.desc': 'SDTM 规范输出',
    'dash.workflow': '三步式工作流程',
    'dash.wf1.title': '上传预处理', 'dash.wf1.desc': '上传百奥知 / 太美 EDC 的 ALS 文件，自动转换为结构化 JSON。',
    'dash.wf2.title': '智能推荐与推理', 'dash.wf2.desc': '4 级级联策略生成 SDTM 变量映射，人工 QC 后确认。',
    'dash.wf3.title': '生成规范文档', 'dash.wf3.desc': '基于 QC 后的映射填充 SDTM Spec 模板，输出完整规范。',

    'upload.title': '上传预处理',
    'upload.lead': '上传 eCRF 数据定义 Excel 文件（ALS），系统将自动识别格式并转换为结构化 JSON 数据。',
    'upload.formats': '支持 .xls / .xlsx / .xlsm',
    'upload.edc.label': 'EDC 系统',
    'upload.raw.desc': '上传 eCRF 数据定义 Excel 文件',
    'upload.drop.text': '拖入文件或点击上传',
    'upload.example.desc': '上传已有的映射示例文件（可选）',
    'upload.example.hint': '用于构建项目专属知识库 · 提升推荐精度',
    'upload.btn': '上传并转换',
    'upload.foot.left': '支持百奥知 4.1 与太美 EDC 格式自动适配',
    'upload.foot.right': '转换结果保存至会话空间',

    'rec.title': '智能推荐与推理',
    'rec.lead': '选择已处理的 JSON 文件，SIRIUS 将通过 4 级级联策略（推荐 + 推理）生成 SDTM 变量映射。',
    'rec.btn.generate': '生成推荐', 'rec.btn.resume': '恢复任务', 'rec.btn.stop': '停止',
    'rec.cascade.title': '4 级级联策略',
    'rec.cascade.1': 'KB 精确匹配', 'rec.cascade.2': 'KB 高置信度', 'rec.cascade.3': 'RAG 增强', 'rec.cascade.4': 'LLM 推理',
    'rec.progress.label': '推荐生成中',
    'rec.foot.left': '级联策略优先使用知识库精确匹配，逐级降级',
    'rec.foot.right': '所有推荐均需人工 QC 确认',

    'spec.title': '生成规范文档',
    'spec.lead': '基于已完成 QC 的 ALS2SDTM 映射文件，填充 SDTM Spec 模板生成完整规范文档。',
    'spec.upload': '上传 ALS2SDTM', 'spec.select': '选择文件...',
    'spec.checkbox': '创建 TEST sheets',
    'spec.btn.generate': '生成 Spec',
    'spec.progress.label': '正在处理',
    'spec.foot.left': '输出文件符合 CDISC SDTM IG 标准格式',
    'spec.foot.right': 'Spec 文件包含变量定义、Codelist、Origin',

    'lib.title': '知识库管理',
    'lib.lead': '管理 RAG 检索的历史映射文档、CDISC 标准定义和项目特定知识。',
    'lib.ref.title': 'CDISC 标准库', 'lib.ref.desc': 'SDTM IG 3.2 / 3.4 标准、变量定义、Controlled Terminology。',
    'lib.hist.title': '历史映射库', 'lib.hist.desc': '已验证的 ALS2SDTM 映射示例，用于 KB 精确匹配与 RAG 增强。',
    'lib.proj.title': '项目知识库', 'lib.proj.desc': '当前会话积累的项目专属映射规则与用户修正反馈。',
    'lib.stat.entries': '知识条目', 'lib.stat.domains': 'SDTM 域', 'lib.stat.coverage': '覆盖率',
    'lib.foot.left': '知识库自动从用户修正中持续学习',
    'lib.foot.right': '支持 Parquet 格式高效检索',

    'guide.title': '使用指南', 'guide.lead': '系统使用方法与最佳实践。', 'guide.open': '打开完整手册',
    'guide.qs.title': '快速开始', 'guide.wf.title': '工作流程详解', 'guide.faq.title': '常见问题',
    'guide.qs.intro': '使用 SIRIUS 的典型工作流程分为三大步骤：',
    'guide.qs.step1.title': '上传 & 预处理',
    'guide.qs.step1.desc': '上传 eCRF 数据定义 Excel（ALS 原始文件），系统自动提取变量信息并生成结构化数据。如有历史项目的 ALS2SDTM 映射文件，也可一并上传作为知识库参考。',
    'guide.qs.step2.title': 'AI 智能推荐',
    'guide.qs.step2.desc1': '选择处理好的文件，选择语言和 AI 模型，点击"生成推荐"。系统自动为每个 CRF 变量推荐 SDTM 映射。完成后请',
    'guide.qs.step2.emph': '及时下载',
    'guide.qs.step2.desc2': ' Excel 结果进行人工审核。',
    'guide.qs.step3.title': '生成 Spec 文档',
    'guide.qs.step3.desc': '上传经过 QC 确认的 ALS2SDTM 映射文件，选择 SDTM 模板，点击"生成 Spec"。系统自动填充各 Domain 表单，生成完整的 SDTM 规范文档。',
    'guide.qs.note.label': '上传注意事项：',
    'guide.qs.note.text': '支持 .xls / .xlsx / .xlsm 格式。目前支持百奥知 4.1 和太美 EDC 系统导出的原始文件，上传前请选择对应的 EDC 系统。',
    'guide.wf.h1': '4 级级联推荐策略',
    'guide.wf.th.level': '级别', 'guide.wf.th.match': '匹配方式', 'guide.wf.th.desc': '说明',
    'guide.wf.l1.match': 'KB 精确匹配', 'guide.wf.l1.desc': 'CRF 表名 + 变量描述完全一致，直接采用历史映射，无需 AI',
    'guide.wf.l2.match': 'KB 高置信度', 'guide.wf.l2.desc': '语义相似度 ≥ 0.85，知识库匹配到高置信度结果',
    'guide.wf.l3.match': 'RAG 增强匹配', 'guide.wf.l3.desc': 'RAG 检索得分 ≥ 0.70，从知识库检索相关片段辅助',
    'guide.wf.l4.match': 'LLM 完整推理', 'guide.wf.l4.desc': '以上均不满足时，调用 AI 模型综合推理',
    'guide.wf.h2': '后处理与校验',
    'guide.wf.li1.label': '域合法性：', 'guide.wf.li1.text': '无效域自动替换并降低置信度',
    'guide.wf.li2.label': '变量名长度：', 'guide.wf.li2.text': '>8 字符时自动修正或截断',
    'guide.wf.li3.label': '批量一致性：', 'guide.wf.li3.text': '12 项跨记录检查（同表域一致性、SUPP 完整性、低置信度比例等）',
    'guide.wf.li4.label': '去重保留：', 'guide.wf.li4.text': '每个变量保留置信度最高的结果',
    'guide.wf.note.label': '提升准确率：',
    'guide.wf.note.text': '上传与当前项目相似的历史映射文件作为项目知识库，可让更多变量在 Level 1-3 直接命中，减少 LLM 调用。',
    'guide.faq.q1': 'Q: 支持哪些 EDC 系统？', 'guide.faq.a1': '目前支持百奥知 4.1 和太美 EDC 两种格式，系统根据页面选择自动适配解析逻辑。',
    'guide.faq.q2': 'Q: ALS2SDTM 示例文件有什么用？', 'guide.faq.a2': '作为项目专属知识库，让 AI 优先参考历史映射经验。上传同适应症的历史文件效果最佳。',
    'guide.faq.q3': 'Q: 推荐结果需要人工审核吗？', 'guide.faq.a3': '是的。AI 推荐仅作为辅助，所有结果都需要人工 QC 确认后才能用于正式 Spec 生成。',
    'guide.faq.q4': 'Q: Spec 模板支持哪些版本？', 'guide.faq.a4': '支持 SDTM IG 3.2 和 IG 3.4 两个版本的模板。系统通过模板文件自动识别版本。',
    'guide.faq.q5': 'Q: 文件会保留多久？', 'guide.faq.a5': '每位用户的上传文件相互独立，关闭浏览器后自动清理。如需立即清理，可点击下方"清理会话文件"。',
    'guide.faq.q6': 'Q: 高亮颜色代表什么？', 'guide.faq.a6': '黄色 = 系统自动填充内容；红色 = SUPP Label 超长或多来源引用，需重点关注；蓝色 = 模板中无对应行的新插入变量。',

    'edc.bioknow': '百奥知 4.1', 'edc.taimei.btn': '太美 EDC',
    'footer.cleanup': '清理会话文件',
    'dialog.sheet.title': '请选择要转换的 Sheet', 'dialog.sheet.confirm': '确认转换',
    'dialog.als.title': '请选择 ALS 文件中的 Sheet', 'dialog.als.confirm': '确认选择',
    'dialog.cancel': '取消'
  },
  en: {
    'sidebar.system': 'Inference Unified System',
    'nav.dashboard': 'Dashboard', 'nav.dashboard.desc': 'Overview & quick actions',
    'nav.upload': 'Upload', 'nav.upload.desc': 'ALS file import & conversion',
    'nav.recommend': 'Recommend', 'nav.recommend.desc': '4-level cascade & inference',
    'nav.specgen': 'Gen Spec', 'nav.specgen.desc': 'SDTM spec document output',
    'nav.library': 'Knowledge Base', 'nav.library.desc': 'Standards / Templates / History',
    'nav.guide': 'User Guide', 'nav.guide.desc': 'System documentation',

    'dash.title': 'SDTM Inference Workbench',
    'dash.lead': 'SIRIUS — SDTM Intelligent Recommendation & Inference Unified System. End-to-end intelligent mapping from CRF variables to SDTM specifications via RAG cascade.',
    'dash.btn.guide': 'User Guide', 'dash.btn.new': 'New Task',
    'kpi.kb': 'KB Documents', 'kpi.kb.sub': 'Indexed example mappings',
    'kpi.acc': 'Accuracy', 'kpi.acc.sub': 'Backend stats not yet wired',
    'kpi.domains': 'Domains', 'kpi.cascade': 'Cascade Levels', 'kpi.cascade.sub': 'KB → RAG → LLM',
    'pipe.upload': 'Upload', 'pipe.upload.desc': 'ALS to JSON',
    'pipe.recommend': 'Recommend', 'pipe.recommend.desc': 'Cascade & inference',
    'pipe.specgen': 'Gen Spec', 'pipe.specgen.desc': 'SDTM spec output',
    'dash.workflow': 'Three-Step Workflow',
    'dash.wf1.title': 'Upload & Preprocess', 'dash.wf1.desc': 'Upload BioKnow / TaiMei EDC ALS files and auto-convert to structured JSON.',
    'dash.wf2.title': 'Recommendation & Inference', 'dash.wf2.desc': '4-level cascade generates SDTM variable mapping, confirmed after manual QC.',
    'dash.wf3.title': 'Generate Spec Document', 'dash.wf3.desc': 'Fill SDTM Spec templates from QC-completed mappings to output a full specification.',

    'upload.title': 'Upload & Preprocess',
    'upload.lead': 'Upload eCRF data definition Excel files (ALS). The system will auto-detect the format and convert to structured JSON.',
    'upload.formats': 'Supports .xls / .xlsx / .xlsm',
    'upload.edc.label': 'EDC System',
    'upload.raw.desc': 'Upload eCRF data definition Excel file',
    'upload.drop.text': 'Drag & drop or click to browse',
    'upload.example.desc': 'Upload existing mapping examples (optional)',
    'upload.example.hint': 'Build project-specific KB · Improve recommendation accuracy',
    'upload.btn': 'Upload & Convert',
    'upload.foot.left': 'Auto-adapts to BioKnow 4.1 & TaiMei EDC formats',
    'upload.foot.right': 'Results saved to session workspace',

    'rec.title': 'Recommendation & Inference',
    'rec.lead': 'Select processed JSON files. SIRIUS generates SDTM variable mapping via 4-level cascade (recommendation + inference).',
    'rec.btn.generate': 'Generate', 'rec.btn.resume': 'Resume Task', 'rec.btn.stop': 'Stop',
    'rec.cascade.title': '4-Level Cascade Strategy',
    'rec.cascade.1': 'KB Exact Match', 'rec.cascade.2': 'KB High Confidence', 'rec.cascade.3': 'RAG Enhanced', 'rec.cascade.4': 'LLM Inference',
    'rec.progress.label': 'Generating recommendations',
    'rec.foot.left': 'Cascade prioritizes KB exact match, falling back progressively',
    'rec.foot.right': 'All recommendations require manual QC',

    'spec.title': 'Generate Spec Document',
    'spec.lead': 'Based on QC-completed ALS2SDTM mapping files, fill SDTM Spec templates to generate complete specification documents.',
    'spec.upload': 'Upload ALS2SDTM', 'spec.select': 'Select file...',
    'spec.checkbox': 'Create TEST sheets',
    'spec.btn.generate': 'Generate Spec',
    'spec.progress.label': 'Processing',
    'spec.foot.left': 'Output complies with CDISC SDTM IG standard format',
    'spec.foot.right': 'Spec includes variable definitions, Codelists, Origins',

    'lib.title': 'Knowledge Base',
    'lib.lead': 'Manage RAG retrieval documents, CDISC standard definitions, and project-specific knowledge.',
    'lib.ref.title': 'CDISC Standards', 'lib.ref.desc': 'SDTM IG 3.2 / 3.4 standards, variable definitions, Controlled Terminology.',
    'lib.hist.title': 'Historical Mappings', 'lib.hist.desc': 'Validated ALS2SDTM examples for KB exact match and RAG enhancement.',
    'lib.proj.title': 'Project KB', 'lib.proj.desc': 'Session-accumulated project-specific mapping rules and user corrections.',
    'lib.stat.entries': 'Entries', 'lib.stat.domains': 'SDTM Domains', 'lib.stat.coverage': 'Coverage',
    'lib.foot.left': 'KB continuously learns from user corrections',
    'lib.foot.right': 'Parquet-based efficient retrieval',

    'guide.title': 'User Guide', 'guide.lead': 'System usage and best practices.', 'guide.open': 'Open Full Manual',
    'guide.qs.title': 'Quick Start', 'guide.wf.title': 'Workflow Details', 'guide.faq.title': 'FAQ',
    'guide.qs.intro': 'The typical SIRIUS workflow has three steps:',
    'guide.qs.step1.title': 'Upload & Preprocess',
    'guide.qs.step1.desc': 'Upload the eCRF data definition Excel (raw ALS file); the system automatically extracts variable information and generates structured data. If you have an ALS2SDTM mapping file from a previous project, you can upload it too as a knowledge-base reference.',
    'guide.qs.step2.title': 'AI Recommendation',
    'guide.qs.step2.desc1': 'Select a processed file, choose the language and AI model, then click "Generate Recommendations". The system automatically recommends an SDTM mapping for every CRF variable. Once done, please',
    'guide.qs.step2.emph': 'download promptly',
    'guide.qs.step2.desc2': ' the Excel results for manual review.',
    'guide.qs.step3.title': 'Generate Spec Document',
    'guide.qs.step3.desc': 'Upload the QC-confirmed ALS2SDTM mapping file, choose an SDTM template, and click "Generate Spec". The system automatically fills in each domain form to produce a complete SDTM specification document.',
    'guide.qs.note.label': 'Upload notes: ',
    'guide.qs.note.text': 'Supports .xls / .xlsx / .xlsm formats. Currently supports raw files exported from BioKnow 4.1 and TaiMei EDC — select the matching EDC system before uploading.',
    'guide.wf.h1': '4-Level Cascade Recommendation Strategy',
    'guide.wf.th.level': 'Level', 'guide.wf.th.match': 'Match Type', 'guide.wf.th.desc': 'Description',
    'guide.wf.l1.match': 'KB Exact Match', 'guide.wf.l1.desc': 'CRF table name + variable description match exactly; the historical mapping is used directly, no AI needed',
    'guide.wf.l2.match': 'KB High Confidence', 'guide.wf.l2.desc': 'Semantic similarity ≥ 0.85; the knowledge base returns a high-confidence match',
    'guide.wf.l3.match': 'RAG-Enhanced Match', 'guide.wf.l3.desc': 'RAG retrieval score ≥ 0.70; relevant snippets retrieved from the knowledge base assist inference',
    'guide.wf.l4.match': 'Full LLM Inference', 'guide.wf.l4.desc': 'When none of the above apply, the AI model performs full reasoning',
    'guide.wf.h2': 'Post-processing & Validation',
    'guide.wf.li1.label': 'Domain validity: ', 'guide.wf.li1.text': 'Invalid domains are automatically replaced and confidence is lowered',
    'guide.wf.li2.label': 'Variable name length: ', 'guide.wf.li2.text': 'Automatically corrected or truncated when over 8 characters',
    'guide.wf.li3.label': 'Batch consistency: ', 'guide.wf.li3.text': '12 cross-record checks (same-table domain consistency, SUPP completeness, low-confidence ratio, etc.)',
    'guide.wf.li4.label': 'Deduplication: ', 'guide.wf.li4.text': 'The highest-confidence result is kept for each variable',
    'guide.wf.note.label': 'Improve accuracy: ',
    'guide.wf.note.text': 'Upload historical mapping files similar to the current project as a project knowledge base — this lets more variables hit directly at Level 1-3, reducing LLM calls.',
    'guide.faq.q1': 'Q: Which EDC systems are supported?', 'guide.faq.a1': 'Currently supports BioKnow 4.1 and TaiMei EDC formats; the system auto-adapts its parsing logic based on the page selection.',
    'guide.faq.q2': 'Q: What are ALS2SDTM example files for?', 'guide.faq.a2': 'They serve as a project-specific knowledge base so the AI prioritizes historical mapping experience. Uploading historical files from the same therapeutic area works best.',
    'guide.faq.q3': 'Q: Do recommendation results need manual review?', 'guide.faq.a3': 'Yes. AI recommendations are only an aid — all results require manual QC confirmation before being used for formal spec generation.',
    'guide.faq.q4': 'Q: Which spec template versions are supported?', 'guide.faq.a4': 'Supports SDTM IG 3.2 and IG 3.4 templates. The system automatically detects the version from the template file.',
    'guide.faq.q5': 'Q: How long are files kept?', 'guide.faq.a5': 'Each user\'s uploaded files are isolated and automatically cleaned up when the browser is closed. To clean up immediately, click "Clean session files" below.',
    'guide.faq.q6': 'Q: What do the highlight colors mean?', 'guide.faq.a6': 'Yellow = auto-filled by the system; red = SUPP Label too long or multi-source reference, needs attention; blue = newly inserted variable with no matching row in the template.',

    'edc.bioknow': 'BioKnow 4.1', 'edc.taimei.btn': 'TaiMei EDC',
    'footer.cleanup': 'Clean session files',
    'dialog.sheet.title': 'Select the Sheet to convert', 'dialog.sheet.confirm': 'Confirm',
    'dialog.als.title': 'Select the Sheet in the ALS file', 'dialog.als.confirm': 'Confirm',
    'dialog.cancel': 'Cancel'
  }
};

function setLang(lang) {
  document.documentElement.lang = lang === 'zh' ? 'zh-CN' : 'en';
  document.querySelectorAll('#langToggle button').forEach((b) => b.classList.toggle('on', b.dataset.l === lang));
  const dict = I18N[lang] || I18N.zh;
  document.querySelectorAll('[data-i18n]').forEach((el) => {
    const key = el.getAttribute('data-i18n');
    if (dict[key] !== undefined) el.textContent = dict[key];
  });
  try { localStorage.setItem('sirius-lang', lang); } catch (e) {}
}

// ==================== Theme ====================
function setTheme(t) {
  document.documentElement.setAttribute('data-theme', t === 'default' ? '' : t);
  document.querySelectorAll('#themeToggle button').forEach((b) => b.classList.toggle('on', b.dataset.t === t));
  try { localStorage.setItem('sirius-theme', t); } catch (e) {}
}

// ==================== Screen navigation ====================
function go(id) {
  document.querySelectorAll('.screen').forEach((s) => s.classList.toggle('active', s.id === 's-' + id));
  document.querySelectorAll('.navitem').forEach((n) => n.classList.toggle('active', n.dataset.screen === id));
  document.querySelectorAll('.pipe-step').forEach((p) => p.classList.toggle('active', p.dataset.go === id));
  const content = document.querySelector('.content');
  if (content) content.scrollTop = 0;
}

function initNav() {
  document.querySelectorAll('.navitem[data-screen]').forEach((n) => n.addEventListener('click', () => go(n.dataset.screen)));
  document.querySelectorAll('[data-go]').forEach((el) => el.addEventListener('click', () => go(el.dataset.go)));
  const toggle = document.getElementById('sidebar-toggle');
  if (toggle) toggle.addEventListener('click', () => document.getElementById('appShell').classList.toggle('sidebar-off'));
  document.querySelectorAll('#langToggle button').forEach((b) => b.addEventListener('click', () => setLang(b.dataset.l)));
  document.querySelectorAll('#themeToggle button').forEach((b) => b.addEventListener('click', () => setTheme(b.dataset.t)));
  document.querySelectorAll('.accordion-trigger').forEach((btn) => {
    btn.addEventListener('click', () => {
      const item = btn.closest('.accordion-item');
      const body = item.querySelector('.accordion-body');
      const isOpen = item.classList.contains('open');
      item.classList.toggle('open', !isOpen);
      btn.setAttribute('aria-expanded', String(!isOpen));
      if (body) body.setAttribute('aria-hidden', String(isOpen));
    });
  });
}

// Restore persisted preferences and wire navigation as early as possible.
(function initPreferences() {
  let t; try { t = localStorage.getItem('sirius-theme'); } catch (e) {}
  if (t) setTheme(t);
  let l; try { l = localStorage.getItem('sirius-lang'); } catch (e) {}
  if (l) setLang(l);
})();

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initNav);
} else {
  initNav();
}

// ==================== Session Management ====================
// 生成或恢复 Session ID，用于隔离不同用户的文件和任务
function getSessionId() {
  let sessionId = sessionStorage.getItem('sirius_session_id');
  if (!sessionId) {
    sessionId = 'sess_' + Date.now() + '_' + Math.random().toString(36).substr(2, 9);
    sessionStorage.setItem('sirius_session_id', sessionId);
  }
  return sessionId;
}

const SESSION_ID = getSessionId();

async function initSession() {
  try {
    await fetch('api/session/init', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: SESSION_ID })
    });
    console.log('[Session] Initialized:', SESSION_ID);
  } catch (e) {
    console.warn('[Session] Init failed:', e);
  }
}

// 封装 fetch，自动带上 Session ID header
function fetchWithSession(url, options = {}) {
  options.headers = {
    ...options.headers,
    'X-Session-ID': SESSION_ID
  };
  return fetch(url, options);
}

/** FastAPI 422/500 的 `detail` 常为对象或数组，直接当 Error 消息会得到 "[object Object]"。 */
function formatApiErrorBody(err) {
  if (!err || typeof err !== "object") return "请求失败";
  const d = err.detail;
  if (typeof d === "string") return d;
  if (Array.isArray(d)) {
    return d
      .map((e) => (e && (e.msg || e.message)) || JSON.stringify(e))
      .join("; ");
  }
  if (d && typeof d === "object") {
    return JSON.stringify(d);
  }
  return err.message || "请求失败";
}

// 页面关闭时安排延迟清理（给刷新操作留出取消窗口）
function scheduleCleanupOnUnload() {
  const data = JSON.stringify({ session_id: SESSION_ID });
  const blob = new Blob([data], { type: 'application/json' });
  navigator.sendBeacon('api/session/schedule-cleanup', blob);
  console.log('[Session] Scheduled cleanup for:', SESSION_ID);
}

// 立即取消待执行的清理（不等待 DOMContentLoaded，尽早执行！）
(function cancelPendingCleanupImmediately() {
  fetch('api/session/cancel-cleanup', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: SESSION_ID })
  }).then(() => {
    console.log('[Session] Cancelled pending cleanup for:', SESSION_ID);
  }).catch(() => {});
})();

// 手动清理 session 资源的函数
async function manualCleanupSession() {
  if (!confirm('确定要清理本次会话上传的所有文件吗？\n\n这将删除：\n- 上传的原始文件\n- 生成的 JSON 映射文件\n- AI 推荐输出文件\n- Spec 输出文件')) {
    return;
  }

  try {
    const response = await fetch('api/session/cleanup', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: SESSION_ID })
    });
    const result = await response.json();

    if (result.status === 'success') {
      showToast({
        type: 'success',
        title: '清理完成',
        message: `已清理 ${result.cleaned_files} 个文件，${result.cleaned_jobs} 个任务`
      });

      if (typeof loadProcessedFiles === 'function') loadProcessedFiles();
      if (typeof loadALSFiles === 'function') loadALSFiles();

      const newSessionId = 'sess_' + Date.now() + '_' + Math.random().toString(36).substr(2, 9);
      sessionStorage.setItem('sirius_session_id', newSessionId);

      setTimeout(() => {
        if (confirm('清理完成。是否刷新页面以开始新的会话？')) {
          window.location.reload();
        }
      }, 500);
    } else {
      throw new Error(result.detail || '清理失败');
    }
  } catch (error) {
    showToast({ type: 'error', title: '清理失败', message: error.message });
  }
}

// 注册页面关闭事件（安排延迟清理）
window.addEventListener('pagehide', scheduleCleanupOnUnload);
window.addEventListener('beforeunload', scheduleCleanupOnUnload);

// 页面加载时：初始化 session
document.addEventListener('DOMContentLoaded', async () => {
  await initSession();
  const cleanupBtn = document.getElementById('cleanup-session');
  if (cleanupBtn) {
    cleanupBtn.addEventListener('click', manualCleanupSession);
  }
});

// ==================== Toast 提示系统 ====================
const toastContainer = document.getElementById("toast-container");

function showToast({ type = "info", title = "", message = "", duration = 4000 }) {
  const icons = { success: "✅", warning: "⚠️", error: "❌", info: "ℹ️" };

  const toast = document.createElement("div");
  toast.className = `toast toast-${type}`;
  toast.innerHTML = `
    <span class="toast-icon">${icons[type]}</span>
    <div class="toast-content">
      ${title ? `<div class="toast-title">${title}</div>` : ""}
      <div class="toast-message">${message}</div>
    </div>
    <button class="toast-close" onclick="this.parentElement.remove()">×</button>
  `;

  toastContainer.appendChild(toast);

  if (duration > 0) {
    setTimeout(() => {
      toast.classList.add("toast-exit");
      setTimeout(() => toast.remove(), 250);
    }, duration);
  }

  return toast;
}

// ==================== Step 1 Elements ====================
const fileRaw = document.getElementById("file-raw");
const dropZoneRaw = document.getElementById("drop-zone-raw");
const filenameRaw = document.getElementById("filename-raw");
const statusRaw = document.getElementById("status-raw");
const btnUploadRaw = document.getElementById("btn-upload-raw");

// EDC 系统切换（百奥知4.1 / 太美）
let selectedEdcSystem = "bioknow";
const edcToggle = document.getElementById("edc-system-toggle");
if (edcToggle) {
  edcToggle.addEventListener("click", (e) => {
    const opt = e.target.closest(".edc-opt");
    if (!opt) return;
    edcToggle.querySelectorAll(".edc-opt").forEach(b => b.classList.remove("active"));
    opt.classList.add("active");
    selectedEdcSystem = opt.dataset.value;
  });
}

// ALS2SDTM 示例
const fileExample = document.getElementById("file-example");
const dropZoneExample = document.getElementById("drop-zone-example");
const filenameExample = document.getElementById("filename-example");
const uploadExampleBtn = document.getElementById("upload-example");
const statusExample = document.getElementById("status-example");

// Sheet 选择对话框
const sheetDialog = document.getElementById("sheet-dialog");
const exampleSheetSelect = document.getElementById("example-sheet-select");
const sheetCancelBtn = document.getElementById("sheet-cancel");
const sheetConfirmBtn = document.getElementById("sheet-confirm");
let uploadedExamplePath = null;

// ==================== Step 2 Elements ====================
const processedSelect = document.getElementById("processed-select");
const refreshBtn = document.getElementById("refresh-files");
const runJobBtn = document.getElementById("run-job");
const cancelJobBtn = document.getElementById("cancel-job");
const progressContainer = document.getElementById("progress-container");
const progressFill = document.getElementById("progress-fill");
const progressPct = document.getElementById("progress-pct");
const progressText = document.getElementById("progress-text");
const downloadArea = document.getElementById("download-area");
const languageSelect = document.getElementById("language-select");
const modelSelect = document.getElementById("model-select");
const elapsedText = document.getElementById("elapsed-text");

// ==================== Step 3 Elements ====================
const alsFileSelect = document.getElementById("als-file-select");
const refreshAlsBtn = document.getElementById("refresh-als-files");
const templateSelect = document.getElementById("template-select");
const refreshTemplatesBtn = document.getElementById("refresh-templates");
const specOutputName = document.getElementById("spec-output-name");
const specProjectName = document.getElementById("spec-project-name");
const highlightMappings = document.getElementById("highlight-mappings");
const createTestSheets = document.getElementById("create-test-sheets");
const runSpecMapperBtn = document.getElementById("run-spec-mapper");
const specProgressContainer = document.getElementById("spec-progress-container");
const specProgressFill = document.getElementById("spec-progress-fill");
const specProgressPct = document.getElementById("spec-progress-pct");
const specProgressText = document.getElementById("spec-progress-text");
const specDownloadArea = document.getElementById("spec-download-area");
const fileAlsOutput = document.getElementById("file-als-output");
const dropZoneAlsOutput = document.getElementById("drop-zone-als-output");
const filenameAlsOutput = document.getElementById("filename-als-output");
const uploadAlsOutputBtn = document.getElementById("upload-als-output");
const statusAlsOutput = document.getElementById("status-als-output");

// ALS Sheet 选择对话框 (Step 3)
const alsSheetDialog = document.getElementById("als-sheet-dialog");
const alsSheetSelect = document.getElementById("als-sheet-select");
const alsSheetCancelBtn = document.getElementById("als-sheet-cancel");
const alsSheetConfirmBtn = document.getElementById("als-sheet-confirm");
let uploadedAlsOutputPath = null;
let selectedAlsSheet = "Sheet1"; // 用户选择的 sheet 名称

let currentJobId = null;
let currentSpecJobId = null;
let pollTimer = null;
let specPollTimer = null;

// ==================== Drop Zone Interactions ====================
const validExtensions = [".xls", ".xlsx", ".xlsm"];

function validateExcelFile(file) {
  if (!file) return false;
  const fileExt = file.name.substring(file.name.lastIndexOf(".")).toLowerCase();
  if (!validExtensions.includes(fileExt)) {
    showToast({
      type: "error",
      title: "文件格式错误",
      message: `不支持的文件格式: ${fileExt}，请上传 Excel 文件 (.xls, .xlsx, .xlsm)`
    });
    return false;
  }
  return true;
}

function setupDropZone(dropZone, fileInput, filenameDisplay, validate = true) {
  if (!dropZone || !fileInput) return;

  dropZone.addEventListener('click', () => fileInput.click());

  dropZone.addEventListener('dragover', e => {
    e.preventDefault();
    dropZone.classList.add('dragover');
  });

  dropZone.addEventListener('dragleave', () => dropZone.classList.remove('dragover'));

  dropZone.addEventListener('drop', e => {
    e.preventDefault();
    dropZone.classList.remove('dragover');
    if (e.dataTransfer.files.length > 0) {
      const file = e.dataTransfer.files[0];
      if (validate && !validateExcelFile(file)) {
        return;
      }
      fileInput.files = e.dataTransfer.files;
      dropZone.classList.add('file-selected');
      if (filenameDisplay) {
        filenameDisplay.textContent = file.name;
      }
      showToast({ type: "info", title: "文件已选择", message: `已选择: ${file.name}`, duration: 2000 });
    }
  });

  fileInput.addEventListener('change', () => {
    if (fileInput.files.length > 0) {
      const file = fileInput.files[0];
      if (validate && !validateExcelFile(file)) {
        fileInput.value = '';
        if (filenameDisplay) filenameDisplay.textContent = '';
        dropZone.classList.remove('file-selected');
        return;
      }
      dropZone.classList.add('file-selected');
      if (filenameDisplay) {
        filenameDisplay.textContent = file.name;
      }
      showToast({ type: "info", title: "文件已选择", message: `已选择: ${file.name}`, duration: 2000 });
    } else {
      dropZone.classList.remove('file-selected');
    }
  });
}

setupDropZone(dropZoneRaw, fileRaw, filenameRaw, true);
setupDropZone(dropZoneExample, fileExample, filenameExample, true);
setupDropZone(dropZoneAlsOutput, fileAlsOutput, filenameAlsOutput, true);

// ==================== Step 1: ALS 原始文件上传 ====================
async function uploadRawFile() {
  const file = fileRaw?.files?.[0];
  if (!file) {
    showToast({ type: "warning", title: "请选择文件", message: "请先选择一个ALS原始文件再上传" });
    statusRaw.textContent = "请选择文件";
    return;
  }

  if (!validateExcelFile(file)) {
    statusRaw.textContent = `文件格式错误`;
    return;
  }

  const formData = new FormData();
  formData.append("file", file);
  statusRaw.textContent = "上传中...";

  const apiEndpoint = selectedEdcSystem === "taimei" ? "api/upload/raw_taimei" : "api/upload/raw";
  const edcLabel = selectedEdcSystem === "taimei" ? "太美" : "百奥知 4.1";

  try {
    const response = await fetchWithSession(apiEndpoint, { method: "POST", body: formData });
    const data = await response.json();

    if (!response.ok) {
      const errorMsg = data.detail || "上传失败";
      let friendlyMsg = errorMsg;
      let toastTitle = "上传失败";

      if (selectedEdcSystem === "taimei") {
        if (errorMsg.includes("Missing required columns")) {
          toastTitle = "列格式错误";
          friendlyMsg = "太美 ALS 文件缺少必需列。请确保文件包含 Forms 和 FormItem 两个 Sheet";
        } else if (errorMsg.includes("sheet") || errorMsg.includes("Forms") || errorMsg.includes("FormItem")) {
          toastTitle = "Sheet 不存在";
          friendlyMsg = "未找到 'Forms' 或 'FormItem' Sheet，请确认这是太美系统导出的 ALS 文件";
        } else if (errorMsg.includes("Unsupported file type")) {
          toastTitle = "文件格式错误";
          friendlyMsg = "不支持的文件格式，请上传 Excel 文件 (.xls, .xlsx, .xlsm)";
        }
      } else {
        if (errorMsg.includes("Missing required columns")) {
          toastTitle = "列格式错误";
          friendlyMsg = "ALS文件缺少必需列。请确保文件包含: 表, 变量, 表名称, 变量名/注释/内嵌表名, 隐藏";
        } else if (errorMsg.includes("Worksheet") || errorMsg.includes("sheet") || errorMsg.includes("eCRF界面")) {
          toastTitle = "Sheet不存在";
          friendlyMsg = "未找到 'eCRF界面' Sheet。请确保Excel文件包含正确的Sheet名称";
        } else if (errorMsg.includes("Unsupported file type")) {
          toastTitle = "文件格式错误";
          friendlyMsg = "不支持的文件格式，请上传 Excel 文件 (.xls, .xlsx, .xlsm)";
        }
      }

      showToast({ type: "error", title: toastTitle, message: friendlyMsg, duration: 6000 });
      throw new Error(friendlyMsg);
    }

    statusRaw.textContent = `✅ 成功：${data.message}`;

    showToast({
      type: "success",
      title: "上传成功",
      message: `[${edcLabel}] 文件已处理并转换为JSON，已自动刷新映射文件列表`
    });

    loadProcessedFiles();

  } catch (err) {
    statusRaw.textContent = `❌ ${err.message}`;
  }
}

if (btnUploadRaw) {
  btnUploadRaw.addEventListener("click", uploadRawFile);
}

// ==================== Step 1: ALS2SDTM 示例上传 ====================
async function uploadExampleFile() {
  const file = fileExample?.files?.[0];
  if (!file) {
    statusExample.textContent = "请选择文件";
    showToast({ type: "warning", title: "请选择文件", message: "请先选择一个文件再上传" });
    return;
  }
  const formData = new FormData();
  formData.append("file", file);
  statusExample.textContent = "上传中...";
  try {
    const response = await fetchWithSession("api/upload/example_raw", { method: "POST", body: formData });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "上传失败");
    uploadedExamplePath = data.stored_to;
    if (exampleSheetSelect && Array.isArray(data.sheets) && data.sheets.length) {
      exampleSheetSelect.innerHTML = "";
      data.sheets.forEach((name) => {
        const opt = document.createElement("option");
        opt.value = name;
        opt.textContent = name;
        exampleSheetSelect.appendChild(opt);
      });
      statusExample.textContent = `✅ 上传成功，请选择 Sheet`;
      sheetDialog?.showModal();
    } else {
      statusExample.textContent = `✅ 上传成功，但未检测到可用 Sheet`;
    }
  } catch (err) {
    statusExample.textContent = `❌ ${err.message}`;
    showToast({ type: "error", title: "上传失败", message: err.message });
  }
}

if (uploadExampleBtn) {
  uploadExampleBtn.addEventListener("click", uploadExampleFile);
}

if (sheetCancelBtn) {
  sheetCancelBtn.addEventListener("click", () => {
    sheetDialog?.close();
    statusExample.textContent = "已取消转换";
  });
}

if (sheetConfirmBtn) {
  sheetConfirmBtn.addEventListener("click", async () => {
    const sheet = exampleSheetSelect?.value || "";
    if (!sheet) {
      showToast({ type: "warning", title: "请选择Sheet", message: "请从列表中选择一个要转换的Sheet" });
      return;
    }
    sheetDialog?.close();
    statusExample.textContent = "转换中...";
    try {
      const response = await fetchWithSession("api/convert-als2sdtm", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ file_path: uploadedExamplePath, sheet_name: sheet }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "转换失败");
      statusExample.textContent = `✅ 转换完成，输出目录：${data.output_dir}`;
      showToast({ type: "success", title: "转换成功", message: `Sheet "${sheet}" 已转换完成，已自动刷新映射文件列表` });
      loadProcessedFiles();
    } catch (err) {
      statusExample.textContent = `❌ ${err.message}`;
      showToast({ type: "error", title: "转换失败", message: err.message, duration: 6000 });
    }
  });
}

// ==================== Step 2: Load Processed Files ====================
async function loadProcessedFiles() {
  processedSelect.innerHTML = "";
  const option = document.createElement("option");
  option.textContent = "加载中...";
  processedSelect.appendChild(option);

  const response = await fetchWithSession("api/processed-files");
  const data = await response.json();
  processedSelect.innerHTML = "";
  if (!data.files.length) {
    const empty = document.createElement("option");
    empty.textContent = "暂无文件";
    processedSelect.appendChild(empty);
    return;
  }
  data.files.forEach((file) => {
    const opt = document.createElement("option");
    opt.value = file;
    opt.textContent = file;
    processedSelect.appendChild(opt);
  });
}

refreshBtn?.addEventListener("click", loadProcessedFiles);
loadProcessedFiles();

// ==================== Step 2: Run Job ====================
function resetProgress() {
  if (progressFill) progressFill.style.width = "0%";
  if (progressPct) progressPct.textContent = "0%";
  if (progressText) progressText.textContent = "";
  if (elapsedText) elapsedText.textContent = "";
  if (downloadArea) downloadArea.innerHTML = "";
  if (progressContainer) progressContainer.style.display = "none";
}

function renderProgress(processed, total) {
  const percentage = total ? Math.round((processed / total) * 100) : 0;
  if (progressFill) progressFill.style.width = `${percentage}%`;
  if (progressPct) progressPct.textContent = `${percentage}%`;
}

async function pollJob(jobId) {
  try {
    const response = await fetchWithSession(`api/jobs/${jobId}`);
    if (!response.ok) {
      throw new Error("无法获取任务状态");
    }
    const data = await response.json();
    renderProgress(data.processed, data.total);

    let displayMessage = "";
    const stateLabels = {
      pending: "⏳ 等待中",
      running: "🔄 处理中",
      completed: "✅ 已完成",
      failed: "❌ 失败",
      cancelled: "⏸️ 已暂停"
    };

    if (data.message) {
      if (data.message.startsWith("📌") || data.message.startsWith("✅") || data.message.startsWith("正在处理")) {
        displayMessage = data.message;
      } else {
        displayMessage = `${stateLabels[data.state] || data.state} - ${data.message}`;
      }
    } else {
      displayMessage = stateLabels[data.state] || data.state;
    }

    if (progressText) progressText.textContent = displayMessage;

    if (data.message && data.message.includes("用时")) {
      const match = data.message.match(/用时\s([\d\.]+s)/);
      if (match && elapsedText) elapsedText.textContent = `耗时：${match[1]}`;
    }

    if (data.state === "completed") {
      if (downloadArea) {
        downloadArea.innerHTML = `
          <div class="success-message">
            <h3>✅ 推荐生成成功！</h3>
            <div class="download-buttons">
              <a href="api/jobs/${jobId}/download?format=excel" target="_blank" class="download-btn">📥 下载 Excel</a>
              <a href="api/jobs/${jobId}/download?format=json" target="_blank" class="download-btn">📥 下载 JSON</a>
            </div>
          </div>
        `;
      }
      pollTimer && clearTimeout(pollTimer);
      return;
    }

    if (data.state === "failed") {
      if (downloadArea) downloadArea.textContent = `❌ ${data.message}`;
      pollTimer && clearTimeout(pollTimer);
      return;
    }

    pollTimer = setTimeout(() => pollJob(jobId), 2000);
  } catch (err) {
    if (progressText) progressText.textContent = `错误：${err.message}`;
  }
}

runJobBtn?.addEventListener("click", async () => {
  const selectedFile = processedSelect?.value;
  if (!selectedFile || selectedFile === "暂无文件") {
    showToast({ type: "warning", title: "请选择文件", message: "请先准备 data/processed 下的 JSON 文件" });
    return;
  }

  resetProgress();
  if (progressContainer) progressContainer.style.display = "block";
  if (progressText) progressText.textContent = "任务已提交...";

  if (resumeJobBtn) resumeJobBtn.style.display = "none";
  lastCancelledJobFile = null;

  const payload = {
    json_file: selectedFile,
    language: languageSelect?.value || "cn",
    model_name: modelSelect?.value || "google/gemini-2.5-flash",
    resume: false,
  };

  const response = await fetchWithSession("api/recommendations", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  if (!response.ok) {
    const err = await response.json();
    if (progressText) progressText.textContent = `提交失败：${err.detail || "未知错误"}`;
    return;
  }

  const data = await response.json();
  currentJobId = data.job_id;
  pollJob(currentJobId);
});

// Cancel Step 2 job
const resumeJobBtn = document.getElementById("resume-job");
let lastCancelledJobFile = null;

cancelJobBtn?.addEventListener("click", async () => {
  if (!currentJobId) {
    showToast({ type: "info", title: "无任务运行", message: "当前没有运行中的任务" });
    return;
  }
  try {
    const response = await fetchWithSession(`api/jobs/${currentJobId}/cancel`, { method: "POST" });
    const data = await response.json();

    if (progressText) progressText.textContent = "任务已终止，进度已保存";
    clearTimeout(pollTimer);
    pollTimer = null;
    currentJobId = null;

    if (data.can_resume && data.json_file) {
      lastCancelledJobFile = data.json_file;
      if (resumeJobBtn) {
        resumeJobBtn.style.display = "inline-flex";
      }
    }

    showToast({ type: "success", title: "任务已终止", message: "进度已保存，可以点击「恢复任务」继续" });
  } catch (err) {
    if (progressText) progressText.textContent = `终止失败：${err.message}`;
  }
});

// Resume cancelled job
resumeJobBtn?.addEventListener("click", async () => {
  const selectedFile = processedSelect?.value || lastCancelledJobFile;
  if (!selectedFile || selectedFile === "暂无文件") {
    showToast({ type: "warning", title: "请选择文件", message: "请选择要恢复的任务文件" });
    return;
  }

  if (progressContainer) progressContainer.style.display = "block";
  if (progressText) progressText.textContent = "正在恢复任务...";
  resumeJobBtn.style.display = "none";

  const payload = {
    json_file: selectedFile,
    language: languageSelect?.value || "cn",
    model_name: modelSelect?.value || "google/gemini-2.5-flash",
    resume: true,
  };

  try {
    const response = await fetchWithSession("api/recommendations", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    if (!response.ok) {
      const err = await response.json();
      if (progressText) progressText.textContent = `恢复失败：${err.detail || "未知错误"}`;
      return;
    }

    const data = await response.json();
    currentJobId = data.job_id;
    lastCancelledJobFile = null;

    showToast({ type: "info", title: "任务已恢复", message: "正在从上次进度继续处理..." });

    pollJob(currentJobId);
  } catch (err) {
    if (progressText) progressText.textContent = `恢复失败：${err.message}`;
  }
});

resetProgress();

// ==================== Step 3: Upload ALS Output ====================
async function uploadAlsOutput() {
  const file = fileAlsOutput?.files?.[0];
  if (!file) {
    statusAlsOutput.textContent = "请选择文件";
    showToast({ type: "warning", title: "请选择文件", message: "请先选择一个文件再上传" });
    return;
  }
  const formData = new FormData();
  formData.append("file", file);
  statusAlsOutput.textContent = "上传中...";
  try {
    const response = await fetchWithSession("api/upload/als_output", { method: "POST", body: formData });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "上传失败");

    uploadedAlsOutputPath = data.stored_to;

    if (Array.isArray(data.sheets) && data.sheets.length > 0) {
      if (data.sheets.length === 1) {
        selectedAlsSheet = data.sheets[0];
        statusAlsOutput.textContent = `✅ 上传成功（Sheet: ${selectedAlsSheet}）`;
        showToast({ type: "success", title: "上传成功", message: `已自动选择 Sheet: ${selectedAlsSheet}` });
      } else {
        alsSheetSelect.innerHTML = "";
        data.sheets.forEach((name) => {
          const opt = document.createElement("option");
          opt.value = name;
          opt.textContent = name;
          alsSheetSelect.appendChild(opt);
        });
        statusAlsOutput.textContent = `✅ 上传成功，请选择 Sheet`;
        alsSheetDialog?.showModal();
      }
    } else {
      selectedAlsSheet = "Sheet1";
      statusAlsOutput.textContent = `✅ 上传成功`;
      showToast({ type: "success", title: "上传成功", message: `文件已保存，使用默认 Sheet` });
    }

    loadALSFiles();
  } catch (err) {
    statusAlsOutput.textContent = `❌ ${err.message}`;
    showToast({ type: "error", title: "上传失败", message: err.message });
  }
}

if (uploadAlsOutputBtn) {
  uploadAlsOutputBtn.addEventListener("click", uploadAlsOutput);
}

if (alsSheetCancelBtn) {
  alsSheetCancelBtn.addEventListener("click", () => {
    alsSheetDialog?.close();
    if (alsSheetSelect?.options?.length > 0) {
      selectedAlsSheet = alsSheetSelect.options[0].value;
    }
    statusAlsOutput.textContent = `已取消选择，使用默认 Sheet: ${selectedAlsSheet}`;
  });
}

if (alsSheetConfirmBtn) {
  alsSheetConfirmBtn.addEventListener("click", () => {
    const sheet = alsSheetSelect?.value || "Sheet1";
    selectedAlsSheet = sheet;
    alsSheetDialog?.close();
    statusAlsOutput.textContent = `✅ 已选择 Sheet: ${selectedAlsSheet}`;
    showToast({ type: "success", title: "Sheet 已选择", message: `将使用 Sheet: ${selectedAlsSheet} 进行映射` });
  });
}

// ==================== Step 3: Load Files ====================
async function loadALSFiles() {
  try {
    const response = await fetchWithSession("api/als-files");
    const data = await response.json();

    alsFileSelect.innerHTML = "";
    if (data.files.length === 0) {
      alsFileSelect.innerHTML = '<option value="">暂无 ALS2SDTM 文件</option>';
      return;
    }

    data.files.forEach(file => {
      const option = document.createElement("option");
      option.value = file.file_id;
      option.textContent = `${file.file_name} (${formatFileSize(file.size)})`;
      alsFileSelect.appendChild(option);
    });
  } catch (error) {
    console.error("Failed to load ALS files:", error);
    alsFileSelect.innerHTML = '<option value="">加载失败</option>';
  }
}

async function loadTemplateFiles() {
  try {
    const response = await fetchWithSession("api/template-files");
    const data = await response.json();

    templateSelect.innerHTML = "";
    if (data.files.length === 0) {
      templateSelect.innerHTML = '<option value="">暂无模板文件</option>';
      return;
    }

    const DEFAULT_TEMPLATE = "SDTM_template_IG3.2.xlsx";
    data.files.forEach(file => {
      const option = document.createElement("option");
      option.value = file.file_id;
      option.textContent = `${file.file_name} (${formatFileSize(file.size)})`;
      if (file.file_name === DEFAULT_TEMPLATE) option.selected = true;
      templateSelect.appendChild(option);
    });
  } catch (error) {
    console.error("Failed to load template files:", error);
    templateSelect.innerHTML = '<option value="">加载失败</option>';
  }
}

function formatFileSize(bytes) {
  if (bytes < 1024) return bytes + ' B';
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
  return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
}

// ==================== Step 3: Run Spec Mapper ====================
runSpecMapperBtn?.addEventListener("click", async () => {
  const alsFile = alsFileSelect?.value;
  const templateFile = templateSelect?.value;
  const outputName = specOutputName?.value?.trim() || 'final_spec';

  if (!alsFile || alsFile === "" || alsFile === "暂无 ALS2SDTM 文件") {
    showToast({ type: "warning", title: "请选择文件", message: "请选择 ALS2SDTM 文件" });
    return;
  }

  if (!templateFile || templateFile === "" || templateFile === "暂无模板文件") {
    showToast({ type: "warning", title: "请选择模板", message: "请选择模板文件" });
    return;
  }

  const projectNameRaw = (specProjectName?.value ?? "").trim() || "web";
  const projectNameRe = /^[A-Za-z0-9_.-]+$/;
  if (projectNameRaw.length < 1 || projectNameRaw.length > 64 || !projectNameRe.test(projectNameRaw)) {
    showToast({
      type: "warning",
      title: "项目名无效",
      message: "请使用 1–64 个字符：字母、数字、下划线、点、连字符（与后端 RAG 回注规则一致）"
    });
    return;
  }

  if (specProgressContainer) specProgressContainer.style.display = "block";
  if (specDownloadArea) specDownloadArea.innerHTML = "";
  if (specProgressFill) specProgressFill.style.width = "0%";
  if (specProgressPct) specProgressPct.textContent = "0%";
  if (specProgressText) specProgressText.textContent = "正在初始化 Spec Mapper...";

  const payload = {
    als_file: alsFile,
    template_file: templateFile,
    output_name: outputName,
    als_sheet: selectedAlsSheet || "Sheet1",
    highlight: true,
    create_test_sheets: createTestSheets?.checked ?? true,
    project_name: projectNameRaw
  };

  try {
    const response = await fetchWithSession("api/spec-mapper/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });

    if (!response.ok) {
      const err = await response.json().catch(() => ({}));
      throw new Error(formatApiErrorBody(err) || "任务提交失败");
    }

    const data = await response.json();
    currentSpecJobId = data.job_id;
    pollSpecJob(currentSpecJobId);

  } catch (error) {
    if (specProgressText) specProgressText.textContent = `错误: ${error.message}`;
    if (specProgressPct) specProgressPct.textContent = "失败";
    showToast({ type: "error", title: "任务提交失败", message: error.message });
  }
});

// Poll Spec Mapper job
function pollSpecJob(jobId) {
  if (specPollTimer) clearInterval(specPollTimer);

  specPollTimer = setInterval(async () => {
    try {
      const response = await fetchWithSession(`api/jobs/${jobId}`);
      if (!response.ok) throw new Error("Job not found");

      const job = await response.json();

      const progress = job.processed || 0;
      const total = job.total || 100;
      const percentage = Math.round((progress / total) * 100);

      if (specProgressFill) specProgressFill.style.width = `${percentage}%`;
      if (specProgressPct) specProgressPct.textContent = `${percentage}%`;
      if (specProgressText) specProgressText.textContent = job.message || "处理中...";

      if (job.state === "completed") {
        clearInterval(specPollTimer);
        if (specProgressPct) specProgressPct.textContent = "✓ 完成";

        if (job.output_excel && specDownloadArea) {
          let downloadButtons = `
            <a href="api/jobs/${jobId}/download?format=excel" class="download-btn">📥 下载 Spec Excel</a>
          `;
          downloadButtons += `
            <button onclick="window.location.reload()" class="download-btn">🔄 刷新页面</button>
          `;
          specDownloadArea.innerHTML = `
            <div class="success-message">
              <h3>✅ Spec 生成成功！</h3>
              <div class="download-buttons">${downloadButtons}</div>
            </div>
          `;
        }

        showToast({ type: "success", title: "Spec 生成成功", message: "SDTM 规范文档已生成完成" });
      } else if (job.state === "failed") {
        clearInterval(specPollTimer);
        if (specProgressPct) specProgressPct.textContent = "✗ 失败";
        const failMsg = typeof job.message === "string" ? job.message : String(job.message ?? "");
        if (specProgressText) specProgressText.textContent = `错误: ${failMsg}`;
        showToast({ type: "error", title: "Spec 生成失败", message: failMsg });
      }

    } catch (error) {
      console.error("Poll error:", error);
    }
  }, 1000);
}

// Refresh buttons
refreshAlsBtn?.addEventListener("click", loadALSFiles);
refreshTemplatesBtn?.addEventListener("click", loadTemplateFiles);

// ==================== Step 3: Delete ALS File ====================
const deleteAlsFileBtn = document.getElementById("delete-als-file");
deleteAlsFileBtn?.addEventListener("click", async () => {
  const selectedFile = alsFileSelect?.value;
  if (!selectedFile || selectedFile === "" || selectedFile === "暂无 ALS2SDTM 文件") {
    showToast({ type: "warning", title: "请选择文件", message: "请先选择一个要删除的文件" });
    return;
  }

  if (!confirm(`确定要删除文件 "${selectedFile}" 吗？此操作不可恢复。`)) {
    return;
  }

  try {
    const response = await fetchWithSession(`api/als-files/${encodeURIComponent(selectedFile)}`, {
      method: "DELETE"
    });

    if (!response.ok) {
      const err = await response.json();
      throw new Error(err.detail || "删除失败");
    }

    showToast({ type: "success", title: "删除成功", message: `文件 "${selectedFile}" 已删除` });

    loadALSFiles();

  } catch (error) {
    showToast({ type: "error", title: "删除失败", message: error.message });
  }
});

// Initialize Step 3
loadALSFiles();
loadTemplateFiles();
