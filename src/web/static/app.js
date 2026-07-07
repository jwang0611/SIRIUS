// ==================== iSDTaiM App v4 ====================
// Premium dark/light theme with advanced animations
// Features: Particle background, scroll animations, enhanced theme toggle

// ==================== Session Management ====================
// 生成或恢复 Session ID，用于隔离不同用户的文件和任务
function getSessionId() {
  let sessionId = sessionStorage.getItem('isdtaim_session_id');
  if (!sessionId) {
    // 生成唯一的 session ID: sess_时间戳_随机字符串
    sessionId = 'sess_' + Date.now() + '_' + Math.random().toString(36).substr(2, 9);
    sessionStorage.setItem('isdtaim_session_id', sessionId);
  }
  return sessionId;
}

const SESSION_ID = getSessionId();

// 初始化 session（通知后端）
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
// 这是关键：必须在延迟清理执行前取消它
(function cancelPendingCleanupImmediately() {
  fetch('api/session/cancel-cleanup', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: SESSION_ID })
  }).then(() => {
    console.log('[Session] Cancelled pending cleanup for:', SESSION_ID);
  }).catch(() => {
    // 忽略错误，可能没有待执行的清理
  });
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
      
      // 刷新文件列表
      if (typeof loadProcessedFiles === 'function') loadProcessedFiles();
      if (typeof loadALSFiles === 'function') loadALSFiles();
      
      // 生成新的 session ID
      const newSessionId = 'sess_' + Date.now() + '_' + Math.random().toString(36).substr(2, 9);
      sessionStorage.setItem('isdtaim_session_id', newSessionId);
      
      // 提示用户页面将刷新
      setTimeout(() => {
        if (confirm('清理完成。是否刷新页面以开始新的会话？')) {
          window.location.reload();
        }
      }, 500);
    } else {
      throw new Error(result.detail || '清理失败');
    }
  } catch (error) {
    showToast({
      type: 'error',
      title: '清理失败',
      message: error.message
    });
  }
}

// 注册页面关闭事件（安排延迟清理）
// pagehide 比 beforeunload 更可靠：bfcache、移动端关闭等场景均能触发
window.addEventListener('pagehide', scheduleCleanupOnUnload);
window.addEventListener('beforeunload', scheduleCleanupOnUnload);

// 页面加载时：初始化 session
document.addEventListener('DOMContentLoaded', async () => {
  // 初始化 session（取消清理已在脚本顶部立即执行）
  await initSession();
  
  // 绑定手动清理按钮事件
  const cleanupBtn = document.getElementById('cleanup-session');
  if (cleanupBtn) {
    cleanupBtn.addEventListener('click', manualCleanupSession);
  }
});

// ==================== Initialize Particle Background ====================
let particleBackground = null;

function initParticles() {
  if (window.ParticleBackground) {
    particleBackground = new ParticleBackground('particles-canvas');
    
    // Update particles theme based on current state
    const currentStep = parseInt(document.body.dataset.step) || 1;
    const isDark = !document.body.classList.contains('light-theme');
    particleBackground.updateTheme(currentStep, isDark);
  }
}

// Initialize particles when DOM is ready
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initParticles);
} else {
  initParticles();
}

// ==================== Scroll-Triggered Animations ====================
function initScrollAnimations() {
  const observer = new IntersectionObserver(
    (entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) {
          entry.target.classList.add('visible');
          // Optional: unobserve after first trigger for performance
          // observer.unobserve(entry.target);
        }
      });
    },
    {
      threshold: 0.1,
      rootMargin: '0px 0px -50px 0px'
    }
  );

  document.querySelectorAll('.animate-on-scroll').forEach((el) => {
    observer.observe(el);
  });
}

// Initialize scroll animations
initScrollAnimations();

// ==================== Enhanced Theme Toggle ====================
const themeToggle = document.getElementById('theme-toggle');
const savedTheme = localStorage.getItem('theme') || 'dark';

// Apply saved theme on load
if (savedTheme === 'light') {
  document.body.classList.add('light-theme');
}

themeToggle?.addEventListener('click', function() {
  // Add switching animation class
  this.classList.add('switching');
  
  // Toggle theme
  document.body.classList.toggle('light-theme');
  const isLight = document.body.classList.contains('light-theme');
  localStorage.setItem('theme', isLight ? 'light' : 'dark');
  
  // Update particle background theme
  if (particleBackground) {
    const currentStep = parseInt(document.body.dataset.step) || 1;
    particleBackground.updateTheme(currentStep, !isLight);
  }
  
  // Remove switching class after animation
  setTimeout(() => {
    this.classList.remove('switching');
  }, 500);
});

// ==================== Toast 提示系统 ====================
const toastContainer = document.getElementById("toast-container");

/**
 * 显示Toast提示
 * @param {Object} options - Toast配置
 * @param {string} options.type - 类型: success | warning | error | info
 * @param {string} options.title - 标题
 * @param {string} options.message - 消息内容
 * @param {number} options.duration - 显示时长(ms)，默认4000
 */
function showToast({ type = "info", title = "", message = "", duration = 4000 }) {
  const icons = {
    success: "✅",
    warning: "⚠️",
    error: "❌",
    info: "ℹ️"
  };

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

  // 自动移除
  if (duration > 0) {
    setTimeout(() => {
      toast.classList.add("toast-exit");
      setTimeout(() => toast.remove(), 250);
    }, duration);
  }

  return toast;
}

// ==================== Tab switching with horizontal slide animation ====================
let isTransitioning = false;
let currentStep = 1;

document.querySelectorAll('.pipe-tab').forEach(tab => {
  tab.addEventListener('click', function() {
    if (isTransitioning) return;
    
    const newStep = parseInt(this.dataset.step);
    const currentPanel = document.querySelector('.panel[style*="block"]') || document.querySelector('.panel:not([style*="none"])');
    const newPanel = document.getElementById('panel-' + newStep);
    
    // Skip if clicking current tab
    if (currentStep === newStep) return;
    
    isTransitioning = true;
    const goingForward = newStep > currentStep;
    
    // Ripple effect
    const ripple = document.createElement('span');
    ripple.className = 'ripple';
    this.appendChild(ripple);
    setTimeout(() => ripple.remove(), 600);
    
    // Update active state
    document.querySelectorAll('.pipe-tab').forEach(t => t.classList.remove('active'));
    this.classList.add('active');
    
    // Update body step for theming
    document.body.dataset.step = newStep;
    
    // Update particle background for new step
    if (particleBackground) {
      const isDark = !document.body.classList.contains('light-theme');
      particleBackground.updateTheme(newStep, isDark);
    }
    
    // Animate out current panel (direction based on navigation)
    if (currentPanel) {
      currentPanel.classList.add(goingForward ? 'panel-out' : 'panel-out-right');
    }
    
    // Wait for exit animation, then show new panel
    setTimeout(() => {
      // Hide all panels and reset classes
      document.querySelectorAll('.panel').forEach(p => {
        p.style.display = 'none';
        p.classList.remove('panel-out', 'panel-out-right');
        p.style.animation = '';
      });
      
      // Show new panel with correct animation direction
      newPanel.style.display = 'block';
      newPanel.style.animation = goingForward 
        ? 'panel-in 0.55s cubic-bezier(0.16, 1, 0.3, 1)' 
        : 'panel-in-left 0.55s cubic-bezier(0.16, 1, 0.3, 1)';
      
      // Re-trigger scroll animations for newly visible panel
      newPanel.querySelectorAll('.animate-on-scroll').forEach(el => {
        el.classList.remove('visible');
        // Force reflow
        void el.offsetWidth;
        // Re-observe
        setTimeout(() => {
          if (isElementInViewport(el)) {
            el.classList.add('visible');
          }
        }, 100);
      });
      
      // Update current step
      currentStep = newStep;
      
      // Allow next transition after animation completes
      setTimeout(() => {
        isTransitioning = false;
      }, 550);
    }, 250);
  });
});

// Helper function to check if element is in viewport
function isElementInViewport(el) {
  const rect = el.getBoundingClientRect();
  return (
    rect.top >= 0 &&
    rect.left >= 0 &&
    rect.bottom <= (window.innerHeight || document.documentElement.clientHeight) &&
    rect.right <= (window.innerWidth || document.documentElement.clientWidth)
  );
}

// ==================== Button click animation ====================
document.querySelectorAll('.btn').forEach(btn => {
  btn.addEventListener('click', function(e) {
    this.classList.add('clicked');
    
    // Create ripple effect at click position
    const rect = this.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const y = e.clientY - rect.top;
    
    const ripple = document.createElement('span');
    ripple.className = 'ripple';
    ripple.style.left = x + 'px';
    ripple.style.top = y + 'px';
    this.appendChild(ripple);
    
    setTimeout(() => {
      this.classList.remove('clicked');
      ripple.remove();
    }, 600);
  });
});

// ==================== Brand shimmer effect every 5 seconds ====================
function brandShimmer() {
  const brand = document.querySelector('.brand-main');
  if (brand) {
    brand.classList.add('shimmer');
    setTimeout(() => brand.classList.remove('shimmer'), 2000);
  }
}
setInterval(brandShimmer, 5000);
setTimeout(brandShimmer, 1500); // Initial shimmer

// ==================== URL Parameter Handling ====================
// Check for step parameter in URL and navigate to the correct step
function handleURLStepParameter() {
  const params = new URLSearchParams(window.location.search);
  const stepParam = params.get('step');
  const alsFileParam = params.get('als_file');
  
  if (stepParam) {
    const targetStep = parseInt(stepParam);
    if (targetStep >= 1 && targetStep <= 3) {
      // Simulate clicking the target tab
      const targetTab = document.querySelector(`.pipe-tab[data-step="${targetStep}"]`);
      if (targetTab) {
        // Use setTimeout to ensure DOM is ready
        setTimeout(() => {
          targetTab.click();
          
          // If als_file parameter is present and we're going to step 3, auto-select the file
          if (alsFileParam && targetStep === 3) {
            setTimeout(() => {
              if (alsFileSelect) {
                // Find and select the option
                for (let i = 0; i < alsFileSelect.options.length; i++) {
                  if (alsFileSelect.options[i].value === alsFileParam) {
                    alsFileSelect.selectedIndex = i;
                    break;
                  }
                }
              }
            }, 500);
          }
          
          // Clear the URL parameters to avoid re-triggering on refresh
          const cleanURL = window.location.pathname;
          window.history.replaceState({}, document.title, cleanURL);
        }, 100);
      }
    }
  }
}

// Call on page load
document.addEventListener('DOMContentLoaded', handleURLStepParameter);

// ==================== Step 1 Elements ====================
// ALS 原始文件
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
      
      // Add visual feedback
      dropZone.classList.add('file-selected');
      
      if (filenameDisplay) {
        filenameDisplay.textContent = file.name;
      }
      showToast({
        type: "info",
        title: "文件已选择",
        message: `已选择: ${file.name}`,
        duration: 2000
      });
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
      
      // Add visual feedback
      dropZone.classList.add('file-selected');
      
      if (filenameDisplay) {
        filenameDisplay.textContent = file.name;
      }
      showToast({
        type: "info",
        title: "文件已选择",
        message: `已选择: ${file.name}`,
        duration: 2000
      });
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
    showToast({
      type: "warning",
      title: "请选择文件",
      message: "请先选择一个ALS原始文件再上传"
    });
    statusRaw.textContent = "请选择文件";
    return;
  }
  
  // 前端预检查：文件格式
  if (!validateExcelFile(file)) {
    statusRaw.textContent = `文件格式错误`;
    return;
  }
  
  const formData = new FormData();
  formData.append("file", file);
  statusRaw.textContent = "上传中...";

  // 根据 EDC 系统选择对应的 API 端点
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
      
      showToast({
        type: "error",
        title: toastTitle,
        message: friendlyMsg,
        duration: 6000
      });
      throw new Error(friendlyMsg);
    }
    
    statusRaw.textContent = `✅ 成功：${data.message}`;
    
    showToast({
      type: "success",
      title: "上传成功",
      message: `[${edcLabel}] 文件已处理并转换为JSON，已自动刷新映射文件列表`
    });
    
    // 自动刷新 Step2 的映射文件列表
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
    showToast({
      type: "warning",
      title: "请选择文件",
      message: "请先选择一个文件再上传"
    });
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
    // 填充 sheet 下拉并弹出对话框
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
    showToast({
      type: "error",
      title: "上传失败",
      message: err.message
    });
  }
}

if (uploadExampleBtn) {
  uploadExampleBtn.addEventListener("click", uploadExampleFile);
}

// 取消选择
if (sheetCancelBtn) {
  sheetCancelBtn.addEventListener("click", () => {
    sheetDialog?.close();
    statusExample.textContent = "已取消转换";
  });
}

// 确认转换
if (sheetConfirmBtn) {
  sheetConfirmBtn.addEventListener("click", async () => {
    const sheet = exampleSheetSelect?.value || "";
    if (!sheet) {
      showToast({
        type: "warning",
        title: "请选择Sheet",
        message: "请从列表中选择一个要转换的Sheet"
      });
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
      
      showToast({
        type: "success",
        title: "转换成功",
        message: `Sheet "${sheet}" 已转换完成，已自动刷新映射文件列表`
      });
      
      // 自动刷新 Step2 的映射文件列表
      loadProcessedFiles();
      
    } catch (err) {
      statusExample.textContent = `❌ ${err.message}`;
      showToast({
        type: "error",
        title: "转换失败",
        message: err.message,
        duration: 6000
      });
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
    
    // Build display message based on state
    let displayMessage = "";
    const stateLabels = {
      pending: "⏳ 等待中",
      running: "🔄 处理中",
      completed: "✅ 已完成",
      failed: "❌ 失败",
      cancelled: "⏸️ 已暂停"
    };
    
    if (data.message) {
      // If message already has emoji/context, show it directly
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
      const fileName = data.output_excel ? data.output_excel.split('/').pop().split('\\').pop() : '';
      
      if (downloadArea) {
        downloadArea.innerHTML = `
          <div class="success-message">
            <h3>✅ 推荐生成成功！</h3>
            <div class="download-buttons">
              <a href="api/jobs/${jobId}/download?format=excel" target="_blank" class="download-btn">
                📥 下载 Excel
              </a>
              <a href="api/jobs/${jobId}/download?format=json" target="_blank" class="download-btn">
                📥 下载 JSON
              </a>
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
    showToast({
      type: "warning",
      title: "请选择文件",
      message: "请先准备 data/processed 下的 JSON 文件"
    });
    return;
  }

  resetProgress();
  if (progressContainer) progressContainer.style.display = "block";
  if (progressText) progressText.textContent = "任务已提交...";
  
  // Hide resume button when starting new job
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
    headers: {
      "Content-Type": "application/json",
    },
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
    showToast({
      type: "info",
      title: "无任务运行",
      message: "当前没有运行中的任务"
    });
    return;
  }
  try {
    const response = await fetchWithSession(`api/jobs/${currentJobId}/cancel`, { method: "POST" });
    const data = await response.json();
    
    if (progressText) progressText.textContent = "任务已终止，进度已保存";
    clearTimeout(pollTimer);
    pollTimer = null;
    currentJobId = null;
    
    // Show resume button if task can be resumed
    if (data.can_resume && data.json_file) {
      lastCancelledJobFile = data.json_file;
      if (resumeJobBtn) {
        resumeJobBtn.style.display = "inline-flex";
      }
    }
    
    showToast({
      type: "success",
      title: "任务已终止",
      message: "进度已保存，可以点击「Resume Task」继续"
    });
  } catch (err) {
    if (progressText) progressText.textContent = `终止失败：${err.message}`;
  }
});

// Resume cancelled job
resumeJobBtn?.addEventListener("click", async () => {
  const selectedFile = processedSelect?.value || lastCancelledJobFile;
  if (!selectedFile || selectedFile === "暂无文件") {
    showToast({
      type: "warning",
      title: "请选择文件",
      message: "请选择要恢复的任务文件"
    });
    return;
  }

  // Don't reset progress - keep showing current progress
  if (progressContainer) progressContainer.style.display = "block";
  if (progressText) progressText.textContent = "正在恢复任务...";
  resumeJobBtn.style.display = "none";

  const payload = {
    json_file: selectedFile,
    language: languageSelect?.value || "cn",
    model_name: modelSelect?.value || "google/gemini-2.5-flash",
    resume: true,  // Enable resume mode
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
    
    showToast({
      type: "info",
      title: "任务已恢复",
      message: "正在从上次进度继续处理..."
    });
    
    // Start polling immediately to get current progress
    pollJob(currentJobId);
  } catch (err) {
    if (progressText) progressText.textContent = `恢复失败：${err.message}`;
  }
});

// Hide progress on initial load
resetProgress();

// ==================== Step 3: Upload ALS Output ====================
async function uploadAlsOutput() {
  const file = fileAlsOutput?.files?.[0];
  if (!file) {
    statusAlsOutput.textContent = "请选择文件";
    showToast({
      type: "warning",
      title: "请选择文件",
      message: "请先选择一个文件再上传"
    });
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
    
    // 检查返回的 sheets 列表
    if (Array.isArray(data.sheets) && data.sheets.length > 0) {
      if (data.sheets.length === 1) {
        // 只有一个 sheet，自动选择，跳过对话框
        selectedAlsSheet = data.sheets[0];
        statusAlsOutput.textContent = `✅ 上传成功（Sheet: ${selectedAlsSheet}）`;
        showToast({
          type: "success",
          title: "上传成功",
          message: `已自动选择 Sheet: ${selectedAlsSheet}`
        });
      } else {
        // 多个 sheet，弹出选择对话框
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
      // 没有检测到 sheets，使用默认值
      selectedAlsSheet = "Sheet1";
      statusAlsOutput.textContent = `✅ 上传成功`;
      showToast({
        type: "success",
        title: "上传成功",
        message: `文件已保存，使用默认 Sheet`
      });
    }
    
    loadALSFiles();
  } catch (err) {
    statusAlsOutput.textContent = `❌ ${err.message}`;
    showToast({
      type: "error",
      title: "上传失败",
      message: err.message
    });
  }
}

if (uploadAlsOutputBtn) {
  uploadAlsOutputBtn.addEventListener("click", uploadAlsOutput);
}

// ALS Sheet 选择对话框事件处理
if (alsSheetCancelBtn) {
  alsSheetCancelBtn.addEventListener("click", () => {
    alsSheetDialog?.close();
    // 使用第一个 sheet 作为默认值
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
    showToast({
      type: "success",
      title: "Sheet 已选择",
      message: `将使用 Sheet: ${selectedAlsSheet} 进行映射`
    });
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
    showToast({
      type: "warning",
      title: "请选择文件",
      message: "请选择 ALS2SDTM 文件"
    });
    return;
  }
  
  if (!templateFile || templateFile === "" || templateFile === "暂无模板文件") {
    showToast({
      type: "warning",
      title: "请选择模板",
      message: "请选择模板文件"
    });
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
  
  // Show progress
  if (specProgressContainer) specProgressContainer.style.display = "block";
  if (specDownloadArea) specDownloadArea.innerHTML = "";
  if (specProgressFill) {
    specProgressFill.style.width = "0%";
  }
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
      headers: {
        "Content-Type": "application/json"
      },
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
    showToast({
      type: "error",
      title: "任务提交失败",
      message: error.message
    });
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
          // 构建下载按钮 HTML
          let downloadButtons = `
            <a href="api/jobs/${jobId}/download?format=excel" class="download-btn">
              📥 下载 Spec Excel
            </a>
          `;
          
          downloadButtons += `
            <button onclick="window.location.reload()" class="download-btn">
              🔄 刷新页面
            </button>
          `;
          
          specDownloadArea.innerHTML = `
            <div class="success-message">
              <h3>✅ Spec 生成成功！</h3>
              <div class="download-buttons">
                ${downloadButtons}
              </div>
            </div>
          `;
        }
        
        showToast({
          type: "success",
          title: "Spec 生成成功",
          message: "SDTM 规范文档已生成完成"
        });
      } else if (job.state === "failed") {
        clearInterval(specPollTimer);
        if (specProgressPct) specProgressPct.textContent = "✗ 失败";
        const failMsg = typeof job.message === "string" ? job.message : String(job.message ?? "");
        if (specProgressText) specProgressText.textContent = `错误: ${failMsg}`;
        showToast({
          type: "error",
          title: "Spec 生成失败",
          message: failMsg
        });
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
    showToast({
      type: "warning",
      title: "请选择文件",
      message: "请先选择一个要删除的文件"
    });
    return;
  }
  
  // Confirm deletion
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
    
    showToast({
      type: "success",
      title: "删除成功",
      message: `文件 "${selectedFile}" 已删除`
    });
    
    // Reload file list
    loadALSFiles();
    
  } catch (error) {
    showToast({
      type: "error",
      title: "删除失败",
      message: error.message
    });
  }
});

// Initialize Step 3
loadALSFiles();
loadTemplateFiles();
