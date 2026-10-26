/* ============================================================
 * app.js — 后台页面公共行为 + 各页面逻辑（真实数据版）
 *
 * 本文件的 DOM 结构 / 样式类名 / 文案骨架沿用早期原型；
 * 与后端相关的差异（数据源换成 real-api.js、登录换成真实 JWT、未开放接口明确
 * 提示而不发假请求）。
 * ============================================================ */
(function () {
  "use strict";

  /* ---------------- 公共：toast ---------------- */
  function toast(msg) {
    let box = document.getElementById("toasts");
    if (!box) {
      box = document.createElement("div");
      box.className = "toasts";
      box.id = "toasts";
      document.body.appendChild(box);
    }
    const el = document.createElement("div");
    el.className = "toast";
    el.innerHTML = '<svg viewBox="0 0 24 24"><path d="M20 6 9 17l-5-5"/></svg><span></span>';
    el.querySelector("span").textContent = msg;
    box.appendChild(el);
    setTimeout(() => { el.style.opacity = "0"; el.style.transition = "opacity .3s"; }, 2200);
    setTimeout(() => el.remove(), 2600);
  }
  window.toast = toast;

  /* 页面级共享件：P1/P2 页面各自一个文件（pages/*.js），统一从这里取，
     避免每加一个页面就再抄一份 toast / 分页 / 转义（函数声明会提升，可前向引用） */
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, (ch) => (
      { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[ch]
    ));
  }

  /* ---------------- 品牌主色：从配置派生整套色阶 ----------------
   * 主色不写死在样式里：改一个 hex，按钮 / 标签 / 图表 / 环形进度全部跟着变。
   * 深浅由 mix() 与黑/白混合得出，避免每个组件各自写死颜色。
   */
  function mix(hex, target, ratio) {
    const n = parseInt(String(hex).slice(1), 16);
    const from = [(n >> 16) & 255, (n >> 8) & 255, n & 255];
    const out = from.map((v, i) => Math.round(v + (target[i] - v) * ratio));
    return "#" + out.map((v) => v.toString(16).padStart(2, "0")).join("");
  }
  function applyBrandTheme(hex, persist) {
    const base = /^#[0-9a-fA-F]{6}$/.test(hex || "") ? hex : "#0f7a52";
    const root = document.documentElement.style;
    const rgb = [(parseInt(base.slice(1, 3), 16)), (parseInt(base.slice(3, 5), 16)), (parseInt(base.slice(5, 7), 16))];
    const theme = {
      green: base,
      "green-rgb": rgb.join(", "),
      "green-dark": mix(base, [0, 0, 0], 0.22),
      "green-soft": mix(base, [255, 255, 255], 0.92),
      "green-line": mix(base, [255, 255, 255], 0.62),
    };
    Object.keys(theme).forEach((key) => root.setProperty("--" + key, theme[key]));
    /* 首屏由 theme-boot.js 读取这份缓存，避免刷新时先绿后蓝 */
    if (persist) {
      try {
        window.localStorage.setItem("moqian_admin_theme", JSON.stringify(theme));
      } catch (e) { /* 隐私模式：只影响首屏恢复，不影响页面主题 */ }
    }
  }
  /* ---------------- 公共：每页条数（10/20/50）与持久化 ----------------
   * 为什么放在这里：分页组件只有一份，但每页条数的"记住上次选择"以前只在
   * 知识源管理做过，其它列表刷新后又回到默认值——看起来像"选了 20 不生效"。
   * 现在统一由这两个函数读写 localStorage，key 由各页面自己给。 */
  const PAGE_SIZES = [10, 20, 50];
  function readPageSize(key, fallback) {
    const base = PAGE_SIZES.indexOf(fallback) >= 0 ? fallback : PAGE_SIZES[0];
    if (!key) return base;
    try {
      const value = parseInt(window.localStorage.getItem(key), 10);
      return PAGE_SIZES.indexOf(value) >= 0 ? value : base;
    } catch (e) {
      return base;   // 隐私模式：本次会话内仍生效，只是不持久化
    }
  }
  function savePageSize(key, size) {
    if (!key || PAGE_SIZES.indexOf(size) < 0) return;
    try {
      window.localStorage.setItem(key, String(size));
    } catch (e) { /* 隐私模式：忽略 */ }
  }

  window.UI = {
    toast: toast,
    pager: renderPager,
    fixedPager: renderFixedPager,
    esc: esc,
    applyBrandTheme: applyBrandTheme,
    mix: mix,
    confirm: confirm,
    readPageSize: readPageSize,
    savePageSize: savePageSize,
  };

  /* ---------------- 公共：业务大类图标砖（格式不出图标，只出文字） ---------------- */
  const CATEGORY_ICONS = {
    manual: '<span class="t-ic t-ic--manual"><svg viewBox="0 0 24 24"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/><path d="M9 7h7M9 11h7"/></svg></span>',
    faq: '<span class="t-ic t-ic--faq"><svg viewBox="0 0 24 24"><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18M3 15h18M12 3v18"/></svg></span>',
    ticket: '<span class="t-ic t-ic--ticket"><svg viewBox="0 0 24 24"><path d="M2 9a3 3 0 0 1 0 6v2a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-2a3 3 0 0 1 0-6V7a2 2 0 0 0-2-2H4a2 2 0 0 0-2 2z"/><path d="M13 5v2M13 11v2M13 17v2"/></svg></span>',
    drawing: '<span class="t-ic t-ic--drawing"><svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><polygon points="16.24 7.76 14.12 14.12 7.76 16.24 9.88 9.88 16.24 7.76"/></svg></span>',
  };
  function categoryIcon(cat) { return CATEGORY_ICONS[cat] || CATEGORY_ICONS.manual; }

  /* 格式标签：非文档任务（自检 / 未注册类型）后端不返回 format，这里统一兜空，
     避免直接读 FORMAT_META[undefined] 把整个列表渲染打断。
     注意 FORMAT_META 由 real-api.js 挂在 window.MOCK 上（不是全局变量），
     各页面在 init 里解构使用，所以这里必须从 window.MOCK 取。 */
  function formatLabel(fmt) {
    if (!fmt) return "";
    const meta = (window.MOCK && window.MOCK.FORMAT_META) || {};
    return (meta[fmt] || {}).label || String(fmt).toUpperCase();
  }

  /* ---------------- 公共：分页组件 ----------------
   * container: 分页容器；onGo(page, pageSize) 由调用方刷新表格
   * sizeKey:   可选，传入后"每页条数"会记到 localStorage，刷新后保持上次选择 */
  function renderPager(container, total, page, pageSize, onGo, sizeKey) {
    if (!container) return;
    const pages = Math.max(1, Math.ceil(total / pageSize));
    if (page > pages) { page = pages; }

    // 页码序列：1 … 当前±1 … 末页
    const seq = [];
    for (let i = 1; i <= pages; i++) {
      if (i === 1 || i === pages || Math.abs(i - page) <= 1) seq.push(i);
      else if (seq[seq.length - 1] !== "…") seq.push("…");
    }

    const start = total === 0 ? 0 : (page - 1) * pageSize + 1;
    const end = Math.min(total, page * pageSize);

    container.innerHTML = `
      <div class="pager__total">共 ${total} 条，显示 ${start}–${end}</div>
      <div class="pager__ctrl">
        <select class="pager__size">
          <option value="10" ${pageSize === 10 ? "selected" : ""}>10 条/页</option>
          <option value="20" ${pageSize === 20 ? "selected" : ""}>20 条/页</option>
          <option value="50" ${pageSize === 50 ? "selected" : ""}>50 条/页</option>
        </select>
        <button class="pgbtn" data-pg="prev" ${page <= 1 ? "disabled" : ""}>‹</button>
        ${seq.map((n) => n === "…"
          ? '<span class="pgellipsis">…</span>'
          : `<button class="pgbtn ${n === page ? "on" : ""}" data-pg="${n}">${n}</button>`).join("")}
        <button class="pgbtn" data-pg="next" ${page >= pages ? "disabled" : ""}>›</button>
      </div>`;

    container.querySelector(".pager__size").addEventListener("change", (e) => {
      const next = parseInt(e.target.value, 10);
      savePageSize(sizeKey, next);
      onGo(1, next);
    });
    container.querySelectorAll("[data-pg]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const v = btn.dataset.pg;
        if (v === "prev") onGo(Math.max(1, page - 1), pageSize);
        else if (v === "next") onGo(Math.min(pages, page + 1), pageSize);
        else onGo(parseInt(v, 10), pageSize);
      });
    });
  }

  /* 固定页大小的分页：总数不超过一页时隐藏（个人域问答/收藏使用）。 */
  function renderFixedPager(container, total, page, pageSize, onGo) {
    if (!container) return;
    if (!total || total <= pageSize) {
      container.hidden = true;
      container.innerHTML = "";
      return;
    }
    container.hidden = false;
    renderPager(container, total, page, pageSize, onGo);
    const size = container.querySelector(".pager__size");
    if (size) size.remove();
  }

  /* ---------------- 公共：二次确认（统一浮层，替代浏览器原生 confirm） ----------------
   * 为什么必须统一：原生 confirm 是浏览器自己的样式，和后台浮层是两套视觉——
   * 同一页里"删除"用浮层、"禁用/重置"弹系统框，看起来像没做完。原型里所有
   * 确认动作都走这里，只有一处实现，文案与按钮样式不会各写各的。
   *
   * 用法：UI.confirm({ title, text, warn, note, tone, icon, okText, okDanger })
   *   text  正文（纯文本，内部转义）
   *   warn  可选，红底警示框（允许少量 HTML，如 <b>）
   *   note  可选，灰绿小字提示框（用来说明"不会发生什么"，不制造恐慌）
   *   tone  "danger" | "warn" | "info"，决定标题图标颜色（默认 warn）
   *   icon  trash / warn / info / lock / logout（默认 warn）
   *   okDanger 确认按钮是否用红色（默认 false，即品牌主色）
   * 返回 Promise<boolean>：确认 true；取消 / Esc / 点遮罩 false。
   */
  const CONFIRM_ICONS = {
    trash: '<path d="M3 6h18"/><path d="M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/><path d="M10 11v6M14 11v6"/>',
    warn: '<path d="M12 9v4M12 17h.01"/><path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/>',
    info: '<circle cx="12" cy="12" r="10"/><path d="M12 16v-4M12 8h.01"/>',
    lock: '<rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>',
    logout: '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><path d="m16 17 5-5-5-5"/><path d="M21 12H9"/>',
  };

  let confirmEl = null;
  let confirmPending = null;

  function settleConfirm(val) {
    if (!confirmPending) return;
    const p = confirmPending;
    confirmPending = null;
    p.mask.hidden = true;
    document.removeEventListener("keydown", p.onKey);
    p.resolve(val);
  }

  function ensureConfirmEl() {
    if (confirmEl) return confirmEl;
    confirmEl = document.createElement("div");
    confirmEl.className = "modal-mask";
    confirmEl.id = "uiConfirm";
    confirmEl.hidden = true;
    confirmEl.innerHTML = `
      <div class="modal" style="width:440px">
        <div class="modal__head" id="uiConfirmHead">
          <svg viewBox="0 0 24 24" id="uiConfirmIcon"></svg>
          <span id="uiConfirmTitle"></span>
        </div>
        <div class="modal__body">
          <p class="modal__text" id="uiConfirmText"></p>
          <div class="failbox" id="uiConfirmWarnBox" hidden>
            <svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><path d="M12 8v4M12 16h.01"/></svg>
            <div id="uiConfirmWarn"></div>
          </div>
          <div class="hint" id="uiConfirmNoteBox" hidden>
            <svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><path d="M12 16v-4M12 8h.01"/></svg>
            <div id="uiConfirmNote"></div>
          </div>
        </div>
        <div class="modal__foot">
          <button class="btn btn--ghost" type="button" id="uiConfirmCancel">取消</button>
          <button class="btn btn--primary" type="button" id="uiConfirmOk">确定</button>
        </div>
      </div>`;
    document.body.appendChild(confirmEl);
    document.getElementById("uiConfirmCancel").addEventListener("click", () => settleConfirm(false));
    document.getElementById("uiConfirmOk").addEventListener("click", () => settleConfirm(true));
    // 点遮罩 = 取消（initModals 也会把它关掉，这里负责给 Promise 一个结果）
    confirmEl.addEventListener("click", (e) => { if (e.target === confirmEl) settleConfirm(false); });
    return confirmEl;
  }

  function confirm(opts) {
    const o = opts || {};
    const mask = ensureConfirmEl();
    // 上一个还没关就又弹一个：先按取消收尾，避免 Promise 悬空
    if (confirmPending) settleConfirm(false);

    const head = document.getElementById("uiConfirmHead");
    head.className = "modal__head" + (o.tone === "danger" ? " modal__head--danger"
      : o.tone === "warn" ? " modal__head--amber" : "");
    document.getElementById("uiConfirmIcon").innerHTML = CONFIRM_ICONS[o.icon || "warn"] || CONFIRM_ICONS.warn;
    document.getElementById("uiConfirmTitle").textContent = o.title || "请确认";
    document.getElementById("uiConfirmText").textContent = o.text || "";

    const warnBox = document.getElementById("uiConfirmWarnBox");
    warnBox.hidden = !o.warn;
    if (o.warn) document.getElementById("uiConfirmWarn").innerHTML = o.warn;
    const noteBox = document.getElementById("uiConfirmNoteBox");
    noteBox.hidden = !o.note;
    if (o.note) document.getElementById("uiConfirmNote").innerHTML = o.note;

    const okBtn = document.getElementById("uiConfirmOk");
    const cancelBtn = document.getElementById("uiConfirmCancel");
    okBtn.textContent = o.okText || "确定";
    okBtn.className = "btn " + (o.okDanger ? "btn--danger" : "btn--primary");
    cancelBtn.textContent = o.cancelText || "取消";

    mask.hidden = false;
    return new Promise((resolve) => {
      const onKey = (e) => { if (e.key === "Escape") settleConfirm(false); };
      confirmPending = { mask, resolve, onKey };
      document.addEventListener("keydown", onKey);
      // 破坏性操作默认聚焦「取消」，避免顺手一个 Enter 就确认了
      (o.okDanger ? cancelBtn : okBtn).focus();
    });
  }

  /* ---------------- 侧栏（选中态 / 折叠 / 占位提示 / 演示数据重置） ----------------
   * 结构与行为已整体迁到 layout.js（单点维护），此处不再保留实现。
   */

  /* ---------------- 公共：弹窗（委托关闭） ---------------- */
  function initModals() {
    document.addEventListener("click", (e) => {
      if (e.target.closest("[data-close]")) {
        const mask = e.target.closest(".modal-mask");
        if (mask) mask.hidden = true;
      }
      if (e.target.classList && e.target.classList.contains("modal-mask")) {
        e.target.hidden = true;
      }
    });
  }

  /* ---------------- 公共：统计卡（数字必须与页面上的数据同源） ----------------
   * 能派生的（知识源数 / 证据单元总数 / 解析中 / 已禁用）一律从数据现算，
   * 派生不出来的（今日问答 / 缺口）由 loadBusinessStats() 从列表接口取 total。
   * 这样往后端加一份知识源，统计卡不会和列表对不上。
   */
  const BUSINESS_STATS = { todayQa: null, openGaps: null };

  function renderStats() {
    const { documents, STATS } = window.MOCK;
    const stats = STATS || {};
    const put = (key, value) => {
      const el = document.querySelector('[data-stat="' + key + '"]');
      if (el) el.textContent = value;
    };
    const counts = stats.jobCounts || window.MOCK.jobCounts || null;
    const inFlight = counts
      ? (counts.running || 0) + (counts.queued || 0)
      : (window.MOCK.jobs || []).filter((j) => j.status === "running" || j.status === "queued").length;
    const disabled = documents.filter((d) => d.status === "disabled").length;
    const docTotal = stats.docTotal != null ? stats.docTotal : documents.length;

    put("docCount", Number(docTotal).toLocaleString());
    put("docDelta", `${inFlight} 个解析任务进行中 · ${disabled} 个已禁用`);
    /* 证据单元总数与「近 7 天新增」都来自 /admin/documents?withStats（服务端统计，
       口径与列表同源）；接口没给的时候保持"—"并说明，不填假数字。 */
    put("chunkCount", stats.totalChunks != null ? Number(stats.totalChunks).toLocaleString() : "—");
    put("chunkDelta", stats.chunksAdded7d != null
      ? "近 7 天新增 " + Number(stats.chunksAdded7d).toLocaleString() + " 条"
      : (stats.totalChunks != null ? "已入库证据单元总数（后端统计）" : "近 7 天新增：本次未取到统计"));
    /* 今日问答 / 待补缺口：由 loadBusinessStats() 异步取 total，未取回前保持"—" */
    put("todayQa", BUSINESS_STATS.todayQa == null ? "—" : BUSINESS_STATS.todayQa.toLocaleString());
    put("qaDelta", BUSINESS_STATS.todayQa == null
      ? "正在统计…"
      : (BUSINESS_STATS.todayQa ? "今日提问轮次（服务端自然日）" : "今日还没有提问"));
    put("gapCount", BUSINESS_STATS.openGaps == null ? "—" : BUSINESS_STATS.openGaps.toLocaleString());
    put("gapDelta", BUSINESS_STATS.openGaps == null
      ? "正在统计…"
      : (BUSINESS_STATS.openGaps ? "未覆盖 · 点进缺口清单逐条处置" : "当前没有未覆盖缺口"));
  }

  /* 今日问答 / 待补知识缺口：没有专门的聚合接口，用现有列表接口的 total 取数
   *   - 今日问答：GET /admin/messages?from=今天&to=今天&pageSize=1 → pagination.total
   *     （服务端按 asked_at 的自然日过滤；管理自测轮次也在内，前端不另算）
   *   - 待补缺口：GET /admin/gaps?status=open&pageSize=1 → pagination.total
   * 这样卡片上的数字和点进去列表的第一页总数必然一致，不再写"未接入"。 */
  function loadBusinessStats() {
    const api = window.AdminAPI;
    if (!api || typeof api.request !== "function") return;
    const now = new Date();
    const pad = (n) => String(n).padStart(2, "0");
    const today = now.getFullYear() + "-" + pad(now.getMonth() + 1) + "-" + pad(now.getDate());

    api.request("GET", "/messages", { query: { from: today, to: today, page: 1, pageSize: 1 } })
      .then((data) => {
        BUSINESS_STATS.todayQa = data && data.pagination
          ? Number(data.pagination.total || 0)
          : 0;
        renderStats();
      })
      .catch(() => {
        BUSINESS_STATS.todayQa = null;
        renderStats();
      });

    api.request("GET", "/gaps", { query: { status: "open", page: 1, pageSize: 1 } })
      .then((data) => {
        BUSINESS_STATS.openGaps = data && data.pagination
          ? Number(data.pagination.total || 0)
          : 0;
        renderStats();
      })
      .catch(() => {
        BUSINESS_STATS.openGaps = null;
        renderStats();
      });
  }

  /* ============================================================
   * 页面：登录（真实 JWT）
   *
   * 与原型相比删掉了两处"原型演示专用"的东西：
   *   - 「演示身份」下拉（真实环境由后端返回登录者，前端不选择身份）；
   *   - 图形验证码（POST /admin/auth/captcha 当前恒返回 enabled:false，
   *     画一个前端自校验的假验证码没有意义，也不构成安全边界）。
   * 登录成功后 JWT 存入 localStorage，由 real-api.js 统一附加到 Authorization 头。
   * ============================================================ */
  function applyLoginBrand(brand) {
    if (!brand) return;
    const nameEl = document.querySelector(".login-logo b");
    if (nameEl && brand.productName) nameEl.textContent = brand.productName + " · 管理台";
    const subEl = document.querySelector(".login-sub");
    if (subEl && brand.orgName) subEl.textContent = brand.orgName + " · 制造业设备售后知识库运营与评测平台";
    const markEl = document.querySelector(".login-logo__mark");
    /* 自定义标识（配置页存的是 SVG/PNG data URL）：登录前也能按品牌展示 */
    if (markEl && /^data:image\/(png|svg\+xml);base64,/.test(String(brand.logo || ""))) {
      markEl.innerHTML = '<img src="' + esc(brand.logo) + '" alt="标识" />';
      markEl.style.background = "#fff";
    }
    if (markEl && brand.primaryColor) markEl.style.color = brand.primaryColor;
    if (brand.productName) document.title = "登录 · " + brand.productName + " 管理台";
  }

  function loadLoginBrand() {
    if (!window.AdminAPI || typeof window.AdminAPI.publicBrand !== "function") return;
    window.AdminAPI.publicBrand()
      .then((brand) => {
        if (!brand) return;
        window.MOCK = window.MOCK || {};
        window.MOCK.systemConfig = window.MOCK.systemConfig || {};
        window.MOCK.systemConfig.brand = brand;
        applyLoginBrand(brand);
        if (window.UI && typeof window.UI.applyBrandTheme === "function") {
          window.UI.applyBrandTheme(brand.primaryColor, true);
        }
      })
      .catch(() => { /* 公开品牌不可读时保留内置文案 */ });
  }

  function initLoginPage() {
    const form = document.getElementById("loginForm");
    if (!form) return;

    // 先用内置/已缓存品牌，再用公开接口同步 app_config 的 brand（登录前也能正确展示）
    applyLoginBrand((window.MOCK && window.MOCK.systemConfig && window.MOCK.systemConfig.brand) || {});
    loadLoginBrand();

    const err = document.getElementById("lgErr");
    const submitBtn = form.querySelector('button[type="submit"]');
    const captchaField = document.getElementById("lgCaptchaField");
    const captchaInput = document.getElementById("lgCaptcha");
    const captchaImg = document.getElementById("lgCaptchaImg");
    const captchaBtn = document.getElementById("lgCaptchaBtn");
    const captcha = { enabled: false, id: null };

    function refreshCaptcha() {
      if (!window.AdminAPI || typeof window.AdminAPI.captcha !== "function") return;
      window.AdminAPI.captcha()
        .then((data) => {
          captcha.enabled = !!(data && data.enabled);
          captcha.id = captcha.enabled ? data.captchaId : null;
          if (captchaField) captchaField.hidden = !captcha.enabled;
          if (captcha.enabled && captchaImg) {
            captchaImg.src = data.image || "";
            if (captchaInput) captchaInput.value = "";
          }
        })
        .catch(() => {
          captcha.enabled = false;
          if (captchaField) captchaField.hidden = true;
        });
    }

    if (captchaBtn) captchaBtn.addEventListener("click", refreshCaptcha);
    refreshCaptcha();

    form.addEventListener("submit", (e) => {
      e.preventDefault();
      const account = document.getElementById("lgUser").value.trim();
      const password = document.getElementById("lgPass").value.trim();
      if (!account || !password) { err.textContent = "请输入账号和密码"; return; }
      if (captcha.enabled && !(captchaInput && captchaInput.value.trim())) {
        err.textContent = "请输入验证码";
        return;
      }
      err.textContent = "";
      if (submitBtn) {
        submitBtn.disabled = true;
        submitBtn.textContent = "登录中…";
      }
      const captchaPayload = captcha.enabled
        ? { captchaId: captcha.id, captchaCode: captchaInput.value.trim() }
        : null;
      window.AdminAPI.login(account, password, captchaPayload)
        .then((data) => {
          window.AdminAuth.set(data.token, data.user);
          /* 普通用户只有「我的账号」可见；治理域页面跳过去会被后端 403 */
          location.href = data.user && data.user.role === "user" ? "account.html" : "index.html";
        })
        .catch((ex) => {
          if (submitBtn) {
            submitBtn.disabled = false;
            submitBtn.textContent = "登 录";
          }
          err.textContent = (ex && ex.message) || "登录失败，请稍后重试";
          /* 后端刚开启验证码时，登录页可能还没拉到挑战：刷新一次把输入框显示出来 */
          if (captcha.enabled || (ex && ex.code === "CAPTCHA_INVALID")) refreshCaptcha();
        });
    });
  }

  /* ============================================================
   * 页面：知识源管理
   * ============================================================ */
  /* 知识源列表的每页条数：刷新后保持用户上次选择（10/20/50）。 */
  const SOURCES_PAGE_SIZE_KEY = "moqian_sources_page_size";

  function initSourcesPage() {
    const { CATEGORY_META, FORMAT_META, CATEGORY_FORMATS, documents } = window.MOCK;
    const tbody = document.getElementById("docRows");
    const empty = document.getElementById("emptyState");
    const pagerEl = document.getElementById("docPager");
    let catFilter = "all";
    let kw = "";
    let page = 1;
    let pageSize = readPageSize(SOURCES_PAGE_SIZE_KEY, 10);
    let showArchived = false; // 默认只看在用资料；归档的勾「显示已归档」才查（D-032）

    /* 归档资料不在默认列表里：开关切换时按 lifecycle 重取一次，替换本地数组后重绘 */
    const archivedSwitch = document.getElementById("showArchived");
    function loadArchived(on) {
      showArchived = on;
      return window.AdminAPI.reloadDocuments(on ? { lifecycle: "archived" } : null)
        .then((data) => {
          const mapped = ((data && data.items) || []).map(window.AdminAPI.toPrototypeDocument);
          documents.splice(0, documents.length, ...mapped);
          page = 1;
          render();
          if (on && !mapped.length) toast("暂无已归档知识源");
        })
        .catch((ex) => {
          if (ex && ex.status === 401) return;
          toast((ex && ex.message) || "已归档列表加载失败");
        });
    }
    if (archivedSwitch) {
      archivedSwitch.addEventListener("change", () => loadArchived(archivedSwitch.checked));
    }

    // 状态列三态：启用中 / 已禁用（在列表、不进检索）/ 已归档（默认不在列表、不进检索）
    function statusTag(s) {
      if (s === "active") return '<span class="tag tag--green"><i class="dot dot--green"></i>启用中</span>';
      if (s === "archived") return '<span class="tag tag--amber"><i class="dot dot--amber"></i>已归档</span>';
      return '<span class="tag tag--gray"><i class="dot dot--gray"></i>已禁用</span>';
    }

    // 版本定位是另一个维度：旧版打标签挂名称区，但状态仍可启用
    function legacyTag(d) {
      if (d.lifecycle !== "legacy") return "";
      const tip = d.legacyTip || "已被新版替代，但仍对老批次设备有效，故保持启用";
      return ` <span class="tag tag--amber legacy-tag" title="${tip}">旧版 ${d.version}</span>`;
    }

    // 操作按钮只跟 status 走，但**槽位固定**：切片 / 重新解析 / 启用·禁用 / 删除。
    // 已禁用时前两个按钮置灰不可点而不是消失——否则按钮整体左移，"启用"会落到
    // 「切片」的位置上，很容易误操作（v0.18）。
    function rowActions(d) {
      // 已归档：默认不在列表里（勾「显示已归档」才看得到），此处只留「恢复」
      if (d.status === "archived") {
        return `<button class="iconbtn iconbtn--text iconbtn--ok" data-restore="${d.id}" title="恢复到列表与检索池">
          <svg viewBox="0 0 24 24"><path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/></svg>恢复
        </button>`;
      }
      const off = d.status === "disabled";
      const delBtn = `<button class="iconbtn iconbtn--text iconbtn--danger" data-archive="${d.id}" title="归档（移出列表与检索，切片与原件保留，可恢复）">
          <svg viewBox="0 0 24 24"><path d="M3 6h18"/><path d="M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/></svg>归档
        </button>`;
      const previewBtn = `<button class="iconbtn iconbtn--text" ${off
        ? 'disabled title="已禁用：启用后才可切片预览"'
        : `data-preview="${d.id}" title="切片预览"`}>
          <svg viewBox="0 0 24 24"><path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7z"/><circle cx="12" cy="12" r="3"/></svg>切片
        </button>`;
      const reparseBtn = `<button class="iconbtn" ${off
        ? 'disabled title="已禁用：启用后才可重新解析"'
        : `data-reparse-doc="${d.id}" title="重新解析"`}>
          <svg viewBox="0 0 24 24"><path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M8 16H3v5"/></svg>
        </button>`;
      const toggleBtn = off
        ? `<button class="iconbtn iconbtn--text iconbtn--ok" data-enable="${d.id}" title="启用">
          <svg viewBox="0 0 24 24"><path d="M12 2v10"/><path d="M18.4 6.6a9 9 0 1 1-12.77.04"/></svg>启用
        </button>`
        : `<button class="iconbtn iconbtn--text iconbtn--danger" data-disable="${d.id}" title="禁用（其证据单元不再参与检索）">
          <svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><path d="m4.9 4.9 14.2 14.2"/></svg>禁用
        </button>`;
      return previewBtn + reparseBtn + toggleBtn + delBtn;
    }

    function getFiltered() {
      return documents.filter((d) => {
        if (catFilter !== "all" && d.category !== catFilter) return false;
        if (kw) {
          const hay = (d.title + d.model + d.batch + CATEGORY_META[d.category].label
            + FORMAT_META[d.format].label).toLowerCase();
          if (hay.indexOf(kw) < 0) return false;
        }
        return true;
      });
    }

    function render() {
      const list = getFiltered();
      // 把页码夹回有效范围：删除/筛选后当前页可能已不存在（否则会出现空表 + 页码正常）
      const pages = Math.max(1, Math.ceil(list.length / pageSize));
      if (page > pages) page = pages;
      empty.hidden = list.length > 0;
      const pageRows = list.slice((page - 1) * pageSize, page * pageSize);
      tbody.innerHTML = pageRows.map((d) => {
        const fmt = FORMAT_META[d.format].label;
        return `
        <tr data-id="${d.id}" class="${d.status === "disabled" || d.status === "archived" ? "row--off" : ""}">
          <td>
            <div class="col-title">
              ${categoryIcon(d.category)}
              <span class="doc-name">${d.title}${legacyTag(d)}<div class="sub">${d.size} · ${fmt} · 更新于 ${d.updated}</div></span>
            </div>
          </td>
          <td><span class="tag tag--gray">${d.model}</span></td>
          <td>${d.batch}</td>
          <td>${d.version}<div class="sub">${d.effective}</div></td>
          <td>${d.chunks.toLocaleString()}</td>
          <td>${statusTag(d.status)}</td>
          <td class="cell-actions"><div class="row-actions">${rowActions(d)}</div></td>
        </tr>`;
      }).join("");
      renderPager(pagerEl, list.length, page, pageSize, (p, ps) => {
        page = p;
        pageSize = ps;
        render();
      }, SOURCES_PAGE_SIZE_KEY);
    }
    render();
    renderStats();   // 统计卡与表格同源：删除等数据变化后两者一起刷新
    loadBusinessStats();   // 今日问答 / 缺口：异步取真实 total

    document.getElementById("typeTabs").addEventListener("click", (e) => {
      const btn = e.target.closest("button[data-cat]");
      if (!btn) return;
      catFilter = btn.dataset.cat;
      page = 1;
      document.querySelectorAll("#typeTabs button").forEach((b) => b.classList.toggle("on", b === btn));
      render();
    });
    document.getElementById("searchInput").addEventListener("input", (e) => {
      kw = e.target.value.trim().toLowerCase();
      page = 1;
      render();
    });

    /* 治理操作统一走真实接口；失败时展示后端文案，不做本地假状态。 */
    function syncDoc(updated) {
      const mapped = window.AdminAPI.toPrototypeDocument(updated);
      const idx = documents.findIndex((x) => x.id === mapped.id);
      if (idx >= 0) documents[idx] = mapped;
      render();
    }

    function toggleDoc(d, enabled) {
      if (!d) return;
      window.AdminAPI.enableDocument(d.id, enabled)
        .then((updated) => {
          syncDoc(updated);
          toast(enabled
            ? "已启用：其证据单元重新参与检索"
            : "已停用（软删除）：证据单元保留，可随时重新启用");
        })
        .catch((ex) => {
          if (ex && ex.status === 401) return;
          toast((ex && ex.message) || (enabled ? "启用失败" : "停用失败"));
        });
    }

    tbody.addEventListener("click", (e) => {
      const previewBtn = e.target.closest("[data-preview]");
      const disableBtn = e.target.closest("[data-disable]");
      const enableBtn = e.target.closest("[data-enable]");
      const reparseBtn = e.target.closest("[data-reparse-doc]");
      const restoreBtn = e.target.closest("[data-restore]");
      const archiveBtn = e.target.closest("[data-archive]");
      if (previewBtn) {
        // 切片预览：真实接口按 id 拉取（document.html?id=doc-x）
        location.href = "document.html?id=" + previewBtn.dataset.preview;
        return;
      }
      if (disableBtn) {
        const d = documents.find((x) => x.id === disableBtn.dataset.disable);
        // 旧版仍服务老批次设备，禁用必须主管二次确认（统一浮层，不用原生 confirm）。
        if (d && d.lifecycle === "legacy") {
          window.UI.confirm({
            title: "禁用旧版手册",
            tone: "warn",
            icon: "warn",
            text: `《${d.title}》是旧版手册，但仍对「${d.batch}」批次的设备生效。`,
            warn: "禁用后，这些老批次设备的问答将不再命中该手册；资料与证据单元仍保留，可随时重新启用。",
            okText: "确认禁用",
            okDanger: true,
          }).then((ok) => { if (ok) toggleDoc(d, false); });
          return;
        }
        toggleDoc(d, false);
        return;
      }
      if (enableBtn) {
        toggleDoc(documents.find((x) => x.id === enableBtn.dataset.enable), true);
        return;
      }
      if (reparseBtn) {
        const id = reparseBtn.dataset.reparseDoc;
        reparseBtn.disabled = true;
        window.AdminAPI.reparseDocument(id)
          .then(() => {
            reparseBtn.disabled = false;
            toast("已重新排队解析：worker 会在下一轮重建切片与向量（进度见「解析任务」）");
          })
          .catch((ex) => {
            reparseBtn.disabled = false;
            if (ex && ex.status === 401) return;
            toast((ex && ex.message) || "重新解析失败，请稍后再试");
          });
        return;
      }
      if (archiveBtn) {
        openArchive(documents.find((x) => x.id === archiveBtn.dataset.archive));
        return;
      }
      if (restoreBtn) {
        restoreDoc(documents.find((x) => x.id === restoreBtn.dataset.restore));
        return;
      }
      const row = e.target.closest("tr[data-id]");
      if (row) location.href = "document.html?id=" + row.dataset.id;
    });

    /* 「归档」= POST /documents/{id}/archive（D-032）：
       与「禁用」不再等价——归档后**从默认列表消失**并退出检索池，切片与原件保留，
       勾选「显示已归档」可找到并恢复；不做物理删除，因为证据单元是已发出答案的引用目标。 */
    function openArchive(d) {
      if (!d) return;
      window.UI.confirm({
        title: "归档知识源",
        tone: "danger",
        icon: "trash",
        text: `即将归档《${d.title}》，其 ${d.chunks.toLocaleString()} 个证据单元会退出检索，问答不再命中这些内容，本列表也不再显示它。`,
        warn: "证据单元与原件都会保留，勾选「显示已归档」可找到并恢复；这不是物理删除。",
        okText: "确认归档",
        okDanger: true,
      }).then((ok) => {
        if (!ok) return;
        window.AdminAPI.archiveDocument(d.id)
          .then(() => {
            const idx = documents.findIndex((x) => x.id === d.id);
            if (idx >= 0) documents.splice(idx, 1);
            render();
            toast("已归档：移出列表与检索池，勾选「显示已归档」可恢复");
          })
          .catch((ex) => {
            if (ex && ex.status === 401) return;
            toast((ex && ex.message) || "归档失败，请稍后再试");
          });
      });
    }

    function restoreDoc(d) {
      if (!d) return;
      window.AdminAPI.restoreDocument(d.id)
        .then(() => {
          /* 恢复后它不再属于"已归档"这一屏：按当前视图直接从本地列表移走 */
          const idx = documents.findIndex((x) => x.id === d.id);
          if (idx >= 0) documents.splice(idx, 1);
          render();
          toast("已恢复：重新出现在默认列表并回到检索池");
        })
        .catch((ex) => {
          if (ex && ex.status === 401) return;
          toast((ex && ex.message) || "恢复失败，请稍后再试");
        });
    }

    /* 上传弹窗：业务大类 → 文件格式联动（格式按扩展名自动识别，可人工修正）
       本批次只接真实 POST /admin/documents（multipart），文件由隐藏的 <input type=file>
       选择；后端当前只接受 PDF，所以选到其它扩展名时在本地就给出明确错误。 */
    const modal = document.getElementById("uploadModal");
    const zone = document.getElementById("uploadZone");
    const zoneDefaultHtml = zone.innerHTML;
    const upErr = document.getElementById("upErr");
    const upCategory = document.getElementById("upCategory");
    const upFormat = document.getElementById("upFormat");
    const upModel = document.getElementById("upModel");
    const upBatch = document.getElementById("upBatch");
    const upVersion = document.getElementById("upVersion");
    const upEffective = document.getElementById("upEffective");
    const fileInput = document.getElementById("fileInput");
    let pickedFile = null;

    function fillFormats(cat, preferred) {
      // 映射缺失时（旧缓存/新分类）不能把格式下拉清空：至少保留按后缀识别的结果
      const mapped = CATEGORY_FORMATS[cat] || [];
      const fmts = mapped.length ? mapped : [preferred].filter(Boolean);
      upFormat.innerHTML = fmts.map((f) => {
        const label = (FORMAT_META[f] && FORMAT_META[f].label) || String(f).toUpperCase();
        return `<option value="${f}" ${f === preferred ? "selected" : ""}>${label}</option>`;
      }).join("");
    }
    function showUpErr(msg) {
      upErr.hidden = !msg;
      if (msg) upErr.querySelector("span").textContent = msg;
      zone.classList.toggle("is-error", !!msg);
    }
    fillFormats(upCategory.value, "pdf");
    upCategory.addEventListener("change", () => {
      fillFormats(upCategory.value, CATEGORY_FORMATS[upCategory.value][0]);
      showUpErr("");
    });

    /* 同型号已有现行版 → 原型要求先处置旧版（FR-K4）。
       处置动作在确认上传后通过 PATCH /admin/documents/{id} 落库：
       「保留为旧版」= superseded（仍参与检索），「保留但禁用」= disabled（退出检索）。 */
    const versionModal = document.getElementById("versionModal");
    let pendingVersions = [];

    function finishUpload() {
      modal.hidden = true;
      versionModal.hidden = true;
      toast("解析任务已创建，正在跳转到任务看板…");
      setTimeout(() => { location.href = "jobs.html"; }, 900);
    }

    function openVersionModal(matched) {
      pendingVersions = matched;
      document.getElementById("verText").textContent =
        `该型号下已有 ${matched.length} 份现行版资料。请先处置旧版，本次会创建解析任务并按选择更新旧版状态：`;
      document.getElementById("verList").innerHTML = matched.map((d) => `
        <div class="ver-item">
          <div class="ver-item__main">
            <b>${esc(d.title)}</b>
            <span class="ver-item__meta">${FORMAT_META[d.format].label} · ${d.version} · ${d.effective} 生效
              · 适用 ${d.batch} · ${d.chunks.toLocaleString()} 个证据单元</span>
          </div>
          <span class="tag tag--green"><i class="dot dot--green"></i>现行版</span>
        </div>`).join("");
      document.querySelectorAll('input[name="verDisposition"]').forEach((r) => { r.disabled = false; });
      document.querySelector('input[name="verDisposition"][value="keep"]').checked = true;
      modal.hidden = true;
      versionModal.hidden = false;
    }

    /* 真实上传：POST /admin/documents（file/category/deviceModel/batch/manualVersion/effectiveDate）。
       版本与生效日期是两个字段：合成一个输入框会把日期整串写进 manual_version。
       后端只做"建档 + 排队"；可选 afterUpload 在成功后执行（版本处置），再统一跳转。 */
    function doUpload(afterUpload) {
      if (!pickedFile) { showUpErr("请先选择要上传的文件：点击上方虚线区域"); return; }
      const btn = document.getElementById("confirmUpload");
      const label = btn.textContent;
      btn.disabled = true;
      btn.textContent = "上传中…";
      showUpErr("");

      const fd = new FormData();
      fd.append("file", pickedFile, pickedFile.name);
      fd.append("category", upCategory.value);
      const model = upModel.value.trim();
      const batch = upBatch.value.trim();
      const version = upVersion.value.trim();
      const effective = upEffective ? upEffective.value.trim() : "";
      if (model) fd.append("deviceModel", model);
      if (batch) fd.append("batch", batch);
      if (version) fd.append("manualVersion", version);
      if (effective) fd.append("effectiveDate", effective);

      window.AdminAPI.uploadDocument(fd)
        .then((res) => {
          if (res && res.status === "already_exists") {
            btn.disabled = false;
            btn.textContent = label;
            showUpErr(`该文件已存在（${res.documentId}），未重复创建解析任务`);
            return null;
          }
          return afterUpload ? afterUpload(res) : true;
        })
        .then((proceeded) => {
          if (proceeded === null) return; // already_exists 分支已就地提示
          finishUpload();
        })
        .catch((ex) => {
          btn.disabled = false;
          btn.textContent = label;
          if (ex && ex.status === 401) return;   // 已跳转登录页
          showUpErr((ex && ex.message) || "上传失败，请稍后重试");
        });
    }

    document.getElementById("confirmVersion").addEventListener("click", () => {
      const choice = document.querySelector('input[name="verDisposition"]:checked');
      const disposition = choice && choice.value === "disable" ? "disabled" : "superseded";
      const olds = pendingVersions.slice();
      doUpload(() => Promise.all(olds.map((d) =>
        window.AdminAPI.patchDocument(d.id, { lifecycle: disposition })
      )).then(() => {
        const idx = documents.findIndex((x) => olds.some((o) => o.id === x.id));
        if (idx >= 0) return window.AdminAPI.reloadDocuments().then((data) => {
          documents.length = 0;
          ((data && data.items) || []).forEach((d) => documents.push(window.AdminAPI.toPrototypeDocument(d)));
        });
        return null;
      }).catch((ex) => {
        if (ex && ex.status === 401) return null;
        toast("旧版处置失败：" + ((ex && ex.message) || "请稍后重试"));
        return null;
      }));
    });

    document.getElementById("btnUpload").addEventListener("click", () => {
      modal.hidden = false;
      pickedFile = null;
      if (fileInput) fileInput.value = "";
      upModel.value = "";
      upBatch.value = "";
      upVersion.value = "";
      if (upEffective) upEffective.value = "";
      zone.innerHTML = zoneDefaultHtml;   // 每次打开回到未选状态，便于演示「未选文件」的错误态
      showUpErr("");
    });

    if (zone) {
      zone.addEventListener("click", () => {
        if (fileInput) fileInput.click();
      });
      zone.addEventListener("dragover", (e) => { e.preventDefault(); zone.classList.add("drag"); });
      zone.addEventListener("dragleave", () => zone.classList.remove("drag"));
      // 拖拽也要真正接收文件：以前只 preventDefault + 去掉高亮，拖进来什么都不会发生
      zone.addEventListener("drop", (e) => {
        e.preventDefault();
        zone.classList.remove("drag");
        const dropped = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
        if (dropped) acceptPickedFile(dropped);
      });
    }

    function showPicked() {
      if (!pickedFile) return;
      const sizeMb = pickedFile.size
        ? (pickedFile.size >= 1048576
          ? (pickedFile.size / 1048576).toFixed(1) + " MB"
          : Math.max(1, Math.round(pickedFile.size / 1024)) + " KB")
        : "";
      const model = upModel.value.trim();
      const version = upVersion.value.trim();
      const catLabel = (CATEGORY_META[upCategory.value] || {}).label || upCategory.value;
      const fmtLabel = (FORMAT_META[upFormat.value] || {}).label || (upFormat.value || "未知格式").toUpperCase();
      const hints = [catLabel, fmtLabel];
      if (model) hints.push("型号 " + model);
      if (version) hints.push("版本 " + version);
      const effective = upEffective ? upEffective.value.trim() : "";
      if (effective) hints.push("生效 " + effective);
      zone.innerHTML = '已选择：<b>' + esc(pickedFile.name) + "</b>（" + sizeMb + "）"
        + '<span style="display:block;margin-top:3px;color:var(--green-dark)">已自动识别：' + esc(hints.join(" · "))
        + "（如识别有误可在下方修正）</span>"
        + '<span style="font-size:11px;color:#8a97a3">（点击重新选择文件）</span>';
    }

    /* 选中/拖入文件后的统一处理：识别分类与格式、清空冲突字段、刷新虚线区提示 */
    function acceptPickedFile(f) {
      if (!f) return;
      const ext = (f.name.split(".").pop() || "").toLowerCase();
      // FAQ 只收 .xlsx；工单导出与数据表格都收 .xlsx/.csv。用户已选 faq/ticket 时保持，
      // 其余情况默认归到「数据表格」，避免 xlsx 一律被当成 FAQ。
      if (ext === "xlsx" || ext === "csv") {
        let cat = "table";
        if (ext === "xlsx" && upCategory.value === "faq") cat = "faq";
        else if (upCategory.value === "ticket") cat = "ticket";
        pickedFile = f;
        upCategory.value = cat;
        fillFormats(cat, ext);
        upModel.value = "";
        upVersion.value = "";
        upBatch.value = "";
        if (upEffective) upEffective.value = "";
        showUpErr("");
        showPicked();
        return;
      }
      // 手册类：PDF / Word(.docx) / Markdown / TXT 走同一条解析链（出处按章节，无页码）
      const docFormat = ext === "markdown" ? "md" : ext;
      if (["pdf", "docx", "md", "txt"].indexOf(docFormat) < 0) {
        pickedFile = null;
        showUpErr(ext === "doc"
          ? "Word 只支持 .docx，请用 Word「另存为」.docx 后上传"
          : "手册与文档支持 PDF / Word(.docx) / Markdown(.md) / TXT(.txt)；FAQ 表格 / 数据表格请选 Excel（.xlsx）或 CSV");
        return;
      }
      pickedFile = f;
      upCategory.value = "manual";
      fillFormats("manual", docFormat);
      const modelMatch = f.name.match(/([A-Za-z]{2,5}[- ]?\d{2,4}[A-Za-z]*|[A-Za-z]{2,5}系列)/);
      const versionMatch = f.name.match(/(Ver\.?\s*\d+(?:\.\d+)*|[Vv]\d+(?:\.\d+)*)/);
      upModel.value = modelMatch ? modelMatch[1].replace(/\s+/g, "") : "";
      upVersion.value = versionMatch ? versionMatch[1].replace(/\s+/g, "") : "";
      upBatch.value = "";
      if (upEffective) upEffective.value = "";
      showUpErr("");
      showPicked();
    }

    if (fileInput) {
      fileInput.addEventListener("change", () => {
        const f = fileInput.files && fileInput.files[0];
        if (f) acceptPickedFile(f);
      });
    }

    document.getElementById("confirmUpload").addEventListener("click", () => {
      // 必填校验：未选文件不得提交
      if (!pickedFile) { showUpErr("请先选择要上传的文件：点击上方虚线区域选择 PDF / Excel"); return; }
      if (!upCategory.value) { showUpErr("请选择业务大类"); return; }
      // 型号留空 = 全型号资料，不参与版本更替；否则同型号 + 同业务大类下的现行版会先弹处置浮层
      const model = upModel.value.trim();
      const sameModelCurrent = model
        ? documents.filter((d) => d.model === model && d.category === upCategory.value && d.lifecycle === "current")
        : [];
      if (sameModelCurrent.length) { openVersionModal(sameModelCurrent); return; }
      doUpload();
    });
  }

  /* ============================================================
   * 页面：解析任务看板
   * ============================================================ */
  const JOBS_PAGE_SIZE_KEY = "moqian_jobs_page_size";

  function initJobsPage() {
    const jobs = window.MOCK.jobs.map((j) => Object.assign({}, j)); // 可变副本
    const tbody = document.getElementById("jobRows");
    const pagerEl = document.getElementById("jobPager");
    const emptyEl = document.getElementById("jobEmpty");
    const noteEl = document.getElementById("jobNote");
    const { FORMAT_META } = window.MOCK;
    const STATUS_LABEL = { running: "运行中", queued: "排队中", failed: "失败", done: "完成", cancelled: "已取消" };
    let page = 1;
    let pageSize = readPageSize(JOBS_PAGE_SIZE_KEY, 10);
    let statusFilter = "all";
    let kw = "";

    const STAGES = [
      { key: "upload", label: "上传" },
      { key: "parse", label: "解析" },
      { key: "chunk", label: "切片" },
      { key: "vector", label: "向量化" },
      { key: "done", label: "入库" },
    ];

    function stageClass(job, key, idx) {
      if (job.status === "queued") return "";
      if (job.status === "cancelled") {
        const stopIdx = STAGES.findIndex((s) => s.key === job.stage);
        return idx < stopIdx ? "done" : "";
      }
      if (job.status === "failed") {
        const failIdx = STAGES.findIndex((s) => s.key === job.stage);
        if (idx < failIdx) return "done";
        if (idx === failIdx) return "failed";
        return "";
      }
      const cur = STAGES.findIndex((s) => s.key === job.stage);
      if (idx < cur) return "done";
      if (idx === cur) return job.stage === "done" ? "done" : "running";
      return "";
    }

    function statusTagOf(j) {
      if (j.status === "running") return '<span class="tag tag--blue"><i class="dot dot--blue"></i>运行中</span>';
      if (j.status === "queued") return '<span class="tag tag--gray"><i class="dot dot--gray"></i>排队中</span>';
      if (j.status === "failed") return '<span class="tag tag--red"><i class="dot dot--red"></i>失败</span>';
      if (j.status === "cancelled") return '<span class="tag tag--amber"><i class="dot dot--amber"></i>已取消</span>';
      return '<span class="tag tag--green"><i class="dot dot--green"></i>完成</span>';
    }

    function actionOf(j) {
      if (j.status === "failed") {
        /* 真实接口：POST /admin/jobs/{id}/retry（仅 failed 可重试） */
        return `<button class="btn btn--ghost btn--sm" data-reparse="${j.id}" title="重新排队（调用 POST /admin/jobs/{id}/retry）">重新解析</button>`;
      }
      if (j.status === "done") {
        return j.docId
          ? `<a class="btn btn--ghost btn--sm" href="document.html?id=${encodeURIComponent(j.docId)}">查看切片</a>`
          : '<span style="font-size:11.5px;color:#8a97a3;white-space:nowrap">源文件未关联</span>';
      }
      if (j.status === "cancelled") {
        return '<span style="font-size:11.5px;color:#8a97a3;white-space:nowrap">已取消</span>';
      }
      if (j.status === "running") {
        if (j.cancelRequested) {
          return '<span style="font-size:11.5px;color:#8a97a3;white-space:nowrap">取消中…</span>';
        }
        return `<button class="btn btn--ghost btn--sm" data-cancel="${j.id}" title="取消任务：运行中会在阶段边界退出">取消任务</button>`;
      }
      return `<button class="btn btn--ghost btn--sm" data-cancel="${j.id}" title="取消排队中的任务">取消任务</button>`;
    }

    function getFiltered() {
      return jobs.filter((j) => {
        if (statusFilter !== "all" && j.status !== statusFilter) return false;
        if (!kw) return true;
        // 非文档任务（自检 / 未注册类型）没有 format，别在这里读 FORMAT_META 崩掉
        const fmt = formatLabel(j.format);
        return (j.docTitle + " " + fmt + " " + STATUS_LABEL[j.status])
          .toLowerCase().indexOf(kw) >= 0;
      });
    }

    const put = (key, val) => {
      const el = document.querySelector('[data-stat="' + key + '"]');
      if (el) el.textContent = val;
    };

    /* 四张状态卡 + 卡片头计数：优先用后端 /admin/jobs?withStats 的 counts
       （本地列表最多取 100 条，counts 才是全量口径）；筛选只影响列表。 */
    function renderStats() {
      const counts = (window.MOCK.STATS && window.MOCK.STATS.jobCounts) || window.MOCK.jobCounts || null;
      const count = (s) => (counts && counts[s] != null ? counts[s] : jobs.filter((j) => j.status === s).length);
      const running = count("running");
      const queued = count("queued");
      const failed = count("failed");
      const done = count("done");
      const cancelled = count("cancelled");
      put("running", running);
      put("runningNote", running ? "正在推进，无需人工操作" : "当前没有运行中的任务");
      put("queued", queued);
      put("queuedNote", queued ? "等待 worker 空闲" : "队列空闲");
      put("failed", failed);
      put("failedNote", failed ? "需处理后重新解析" : "当前没有失败任务");
      put("done", done);
      put("doneNote", "已完成入库，可查看切片");
      const total = counts
        ? (counts.running || 0) + (counts.queued || 0) + (counts.failed || 0)
          + (counts.done || 0) + (counts.cancelled || 0)
        : jobs.length;
      noteEl.textContent = "共 " + total + " 个任务 · 运行中 " + running
        + " · 排队中 " + queued + " · 失败 " + failed + " · 完成 " + done
        + " · 已取消 " + cancelled;
    }

    function render() {
      const list = getFiltered();
      const total = list.length;
      const pages = Math.max(1, Math.ceil(total / pageSize));
      if (page > pages) page = pages;
      const pageRows = list.slice((page - 1) * pageSize, page * pageSize);
      emptyEl.hidden = total > 0;
      tbody.innerHTML = pageRows.map((j) => {
        const flow = STAGES.map((s, i) => {
          const cls = stageClass(j, s.key, i);
          const noContent = cls === "done"
            ? '<svg viewBox="0 0 24 24" style="width:12px;height:12px"><path d="M20 6 9 17l-5-5"/></svg>'
            : i + 1;
          return `<div class="flow__step ${cls}"><span class="flow__no">${noContent}</span>${s.label}</div>`
            + (i < STAGES.length - 1 ? '<div class="flow__line"></div>' : "");
        }).join("");

        const barCls = j.status === "done" ? "progress progress--green" : "progress";
        const detail = j.status === "failed"
          ? `<div class="failbox"><svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><path d="M12 8v4M12 16h.01"/></svg><div>${j.error}</div></div>`
          : `<div style="font-size:11.5px;color:#8a97a3;margin-top:6px">${j.note || ""} ${statusTagOf(j)}</div>`;

        // 有知识源：格式 · 时间；非文档任务没有格式与开始时间，副行整条省略（不编造 "PDF"、不留空破折号）
        const subBits = [formatLabel(j.format), j.startedAt === "—" ? "" : j.startedAt]
          .filter(Boolean).join(" · ");
        const subLine = subBits ? `<div class="sub">${subBits}</div>` : "";
        return `
        <tr data-id="${j.id}">
          <td>
            <div class="col-title" style="cursor:default">
              ${categoryIcon(j.category)}
              <span>${j.docTitle}${subLine}</span>
            </div>
          </td>
          <td><div class="flow">${flow}</div>${detail}</td>
          <td>
            <div style="display:flex;align-items:center;gap:9px">
              <div class="${barCls}"><i style="width:${j.progress}%"></i></div>
              <span style="font-size:12px;color:#52606d;font-variant-numeric:tabular-nums">${j.progress}%</span>
            </div>
          </td>
          <td style="font-size:12px;color:#52606d;white-space:nowrap">${j.startedAt}</td>
          <td class="cell-actions"><div class="row-actions">${actionOf(j)}</div></td>
        </tr>`;
      }).join("");
      renderPager(pagerEl, total, page, pageSize, (p, ps) => {
        page = p;
        pageSize = ps;   // 以前这里忽略了 ps，"10 条/页" 改成 20 没有反应
        render();
      }, JOBS_PAGE_SIZE_KEY);
      renderStats();
    }
    render();

    /* 状态筛选 + 关键词搜索：与其它列表页同一套交互，两者都把页码重置到第 1 页 */
    document.getElementById("jobStatusTabs").addEventListener("click", (e) => {
      const btn = e.target.closest("button[data-js]");
      if (!btn) return;
      statusFilter = btn.dataset.js;
      page = 1;
      document.querySelectorAll("#jobStatusTabs button").forEach((b) => b.classList.toggle("on", b === btn));
      render();
    });
    document.getElementById("searchInput").addEventListener("input", (e) => {
      kw = e.target.value.trim().toLowerCase();
      page = 1;
      render();
    });

    tbody.addEventListener("click", (e) => {
      const re = e.target.closest("[data-reparse]");
      const ca = e.target.closest("[data-cancel]");
      if (re) {
        if (re.disabled) return;
        const id = re.dataset.reparse;
        re.disabled = true;
        /* 真实接口：只有 failed 能重试；成功后用返回的 Job 更新本行，不伪造进度 */
        window.AdminAPI.retryJob(id)
          .then((job) => {
            const mapped = window.AdminAPI.toPrototypeJob(job);
            const idx = jobs.findIndex((x) => x.id === mapped.id);
            if (idx >= 0) jobs[idx] = mapped;
            render();
            toast("已重新排队：worker 会在下一轮领取任务");
          })
          .catch((ex) => {
            re.disabled = false;
            if (ex && ex.status === 401) return;
            toast((ex && ex.message) || "重试失败，请稍后再试");
          });
      }
      if (ca) {
        const id = ca.dataset.cancel;
        ca.disabled = true;
        window.AdminAPI.cancelJob(id)
          .then((job) => {
            const mapped = window.AdminAPI.toPrototypeJob(job);
            const idx = jobs.findIndex((x) => x.id === mapped.id);
            if (idx >= 0) jobs[idx] = mapped;
            render();
            toast(mapped.cancelRequested
              ? "已请求取消：运行中任务会在阶段边界退出"
              : "任务已取消");
          })
          .catch((ex) => {
            ca.disabled = false;
            if (ex && ex.status === 401) return;
            toast((ex && ex.message) || "取消失败，请稍后再试");
          });
      }
    });

    const refreshBtn = document.getElementById("btnRefresh");
    if (refreshBtn) {
      refreshBtn.addEventListener("click", () => {
        window.AdminAPI.reloadJobs()
          .then((data) => {
            jobs.length = 0;
            ((data && data.items) || []).forEach((j) => jobs.push(window.AdminAPI.toPrototypeJob(j)));
            const counts = data && data.stats ? data.stats.counts : null;
            if (counts) {
              window.MOCK.jobCounts = counts;
              window.MOCK.STATS = Object.assign({}, window.MOCK.STATS, { jobCounts: counts });
            }
            render();
            if (window.AdminLayout && window.AdminLayout.refreshBadges) window.AdminLayout.refreshBadges();
            toast("已刷新任务状态（数据来自 /admin/jobs）");
          })
          .catch((ex) => {
            if (ex && ex.status === 401) return;
            toast((ex && ex.message) || "刷新失败，请稍后再试");
          });
      });
    }
  }

  /* ============================================================
   * 页面：切片预览与治理
   * ============================================================ */
  function initDocumentPage() {
    const { documents, FORMAT_META, chunksByDoc } = window.MOCK;
    // 按 ?id= 取知识源；直接打开本页（无参数）时回退到第一份，便于单独调试
    const docId = new URLSearchParams(location.search).get("id");
    const doc = documents.find((d) => d.id === docId) || documents[0];
    const chunks = (chunksByDoc[doc.id] || []).map((c) => Object.assign({}, c));
    const listEl = document.getElementById("chunkList");
    const detailEl = document.getElementById("chunkDetail");
    let selectedId = chunks.length ? chunks[0].id : null;

    /* ---------------- 原文预览（PDF 原生阅读器 / 文本） ---------------- */
    let previewUrl = null;
    let previewBlob = null;
    let previewPage = null;
    let previewPageSize = null;
    let previewZoom = null;
    let previewObserver = null;

    function pageNo(chunk) {
      const matched = /P(\d+)/.exec(String(chunk.page || ""));
      return matched ? Number(matched[1]) : null;
    }

    function pageLinkHtml(chunk) {
      const no = pageNo(chunk);
      if (!no || !doc.hasOriginal) return "";
      return `<button type="button" class="chunk-page-link" data-preview-page="${no}"`
        + ` title="在原文中查看 ${chunk.page}">${chunk.page}</button>`;
    }

    function closePreview() {
      if (document.fullscreenElement) {
        document.exitFullscreen().catch(() => {});
      }
      document.getElementById("previewModal").hidden = true;
      document.getElementById("previewBody").innerHTML = "";
      if (previewObserver) {
        previewObserver.disconnect();
        previewObserver = null;
      }
      if (previewUrl) {
        URL.revokeObjectURL(previewUrl);
        previewUrl = null;
      }
      previewBlob = null;
      previewPage = null;
      previewPageSize = null;
      previewZoom = null;
    }

    /* 「适应页面」缩放：按页面尺寸算出让整页刚好放进预览框的 zoom 百分比。
       Edge 阅读器 100% 时一页 pt 约渲染成 1.21 个 CSS px（96dpi 再叠加阅读器
       自身的缩放基线），再扣掉它自带的工具栏与留白 52px，最后乘 0.94 收边，
       保证整页可见且不出现滚动条。 */
    function fitZoomPercent(frame) {
      if (!frame || !previewPageSize
          || !previewPageSize.pageWidth || !previewPageSize.pageHeight) {
        return null;
      }
      const pt2px = 1.21;
      const availW = Math.max(160, (frame.clientWidth || 0) - 16);
      const availH = Math.max(160, (frame.clientHeight || 0) - 52);
      const fit = Math.min(
        availW / (previewPageSize.pageWidth * pt2px),
        availH / (previewPageSize.pageHeight * pt2px)
      );
      return Math.max(20, Math.min(400, Math.floor(fit * 100 * 0.94)));
    }

    /* iframe 里的阅读器只认数值 zoom：page-fit/Fit/FitV 在 Edge 都被忽略，
       所以「适应页面」要自己算百分比；换尺寸后必须重载一次才会重新排版。
       尺寸用 ResizeObserver 拿，才能保证量到的是重排之后的真实大小
       （全屏切换那一刻布局还没生效，直接量会按旧尺寸算小）。 */
    function applyFit(force) {
      const frame = document.querySelector(".doc-preview__frame");
      if (!frame || !previewBlob) return;
      const zoom = fitZoomPercent(frame);
      if (!force && zoom === previewZoom) return;
      previewZoom = zoom;
      const page = previewPage || 1;
      const oldUrl = previewUrl;
      previewUrl = URL.createObjectURL(previewBlob);
      frame.src = previewUrl + "#page=" + page + (zoom ? "&zoom=" + zoom : "");
      if (oldUrl) setTimeout(() => URL.revokeObjectURL(oldUrl), 1500);
    }

    function watchPreviewFrame() {
      if (previewObserver) previewObserver.disconnect();
      const frame = document.querySelector(".doc-preview__frame");
      previewZoom = null;
      if (!frame) return;
      if (!window.ResizeObserver) {
        applyFit(true);
        return;
      }
      previewObserver = new ResizeObserver(() => applyFit(false));
      previewObserver.observe(frame);
    }

    function openPreview(page) {
      if (!doc.hasOriginal) {
        toast("该知识源没有原件（由工单沉淀生成），无法预览");
        return;
      }
      const modal = document.getElementById("previewModal");
      const body = document.getElementById("previewBody");
      const fmt = (FORMAT_META[doc.format] || {}).label || doc.format || "原件";
      document.getElementById("previewTitle").textContent = "原文预览 · " + doc.title;
      document.getElementById("previewMeta").textContent =
        fmt + (doc.size && doc.size !== "—" ? " · " + doc.size : "");
      body.innerHTML = '<div class="doc-preview__empty">正在读取原件…</div>';
      modal.hidden = false;
      previewPage = page || null;
      const newTabBtn = document.getElementById("previewNewTab");
      newTabBtn.disabled = doc.format !== "pdf";
      newTabBtn.title = doc.format === "pdf" ? "在浏览器新标签中打开原件" : "仅 PDF 支持新标签打开";
      if (previewUrl) {
        URL.revokeObjectURL(previewUrl);
        previewUrl = null;
      }
      const fail = (message) => {
        body.innerHTML = '<div class="doc-preview__empty"></div>';
        body.firstElementChild.textContent = message;
      };

      if (doc.format === "pdf") {
        Promise.all([
          window.AdminAPI.documentPreview(doc.id).catch(() => null),
          window.AdminAPI.documentFile(doc.id),
        ]).then((result) => {
          const meta = result[0];
          previewPageSize = meta && meta.kind === "pdf" ? meta : null;
          previewBlob = result[1];
          body.innerHTML = '<iframe class="doc-preview__frame" title="原件预览"></iframe>';
          watchPreviewFrame();
        }).catch((ex) => {
          if (ex && ex.status === 401) return;
          fail((ex && ex.message) || "原件读取失败，请稍后再试");
        });
        return;
      }

      window.AdminAPI.documentPreview(doc.id).then((result) => {
        if (result.kind === "text") {
          body.innerHTML = '<pre class="doc-preview__text"></pre>';
          body.firstElementChild.textContent = result.text
            + (result.truncated ? "\n\n…（原文超过 1MB，已截断）" : "");
          return;
        }
        fail(result.message || "该原件暂不支持在线预览，请下载后查看。");
      }).catch((ex) => {
        if (ex && ex.status === 401) return;
        fail((ex && ex.message) || "原件读取失败，请稍后再试");
      });
    }

    document.getElementById("btnPreviewOriginal").addEventListener("click", () => openPreview());
    document.getElementById("previewClose").addEventListener("click", closePreview);
    document.getElementById("previewModal").addEventListener("click", (e) => {
      if (e.target.id === "previewModal") closePreview();
    });
    document.getElementById("previewNewTab").addEventListener("click", () => {
      if (!previewUrl) {
        toast("当前预览没有可新标签打开的原件");
        return;
      }
      const win = window.open(
        previewUrl + (previewPage ? "#page=" + previewPage : ""),
        "_blank"
      );
      if (!win) toast("浏览器拦截了新标签，请允许本站弹窗后再试");
    });
    document.getElementById("previewFullscreen").addEventListener("click", () => {
      const box = document.querySelector(".doc-preview");
      if (document.fullscreenElement) {
        document.exitFullscreen().catch(() => {});
        return;
      }
      if (!box || !box.requestFullscreen) {
        toast("当前浏览器不支持页面全屏，可用「新标签打开」");
        return;
      }
      box.requestFullscreen().catch(() => {
        toast("全屏被浏览器拒绝，可用「新标签打开」");
      });
    });
    document.addEventListener("fullscreenchange", () => {
      const btn = document.getElementById("previewFullscreen");
      if (btn) btn.textContent = document.fullscreenElement ? "退出全屏" : "全屏";
      /* 预览框尺寸一变，ResizeObserver 会重算「适应页面」的 zoom 并重载阅读器。 */
    });
    document.getElementById("previewDownload").addEventListener("click", (e) => {
      e.preventDefault();
      if (!doc.hasOriginal) return;
      const suffix = { pdf: ".pdf", word: ".docx", excel: ".xlsx" }[doc.format] || "";
      window.AdminAPI.documentFile(doc.id).then((blob) => {
        const url = URL.createObjectURL(blob);
        const link = document.createElement("a");
        link.href = url;
        link.download = doc.title + suffix;
        document.body.appendChild(link);
        link.click();
        link.remove();
        setTimeout(() => URL.revokeObjectURL(url), 1500);
      }).catch((ex) => {
        if (ex && ex.status === 401) return;
        toast((ex && ex.message) || "原件下载失败，请稍后再试");
      });
    });

    /* ---------------- 文档级信息：面包屑 / 版本提醒 / 横幅 / 标签 ----------------
     * 全部依当前知识源的数据渲染，换一份知识源整页跟着变（此前写死为 doc-1）。
     */
    function currentSibling() {
      return documents.find((d) => d !== doc && d.model === doc.model && d.lifecycle === "current");
    }
    function legacySibling() {
      return documents.find((d) => d !== doc && d.model === doc.model && d.lifecycle === "legacy");
    }

    function renderBanner() {
      document.getElementById("docCrumb").textContent = doc.title;
      document.title = "切片预览 · " + doc.title;
      document.getElementById("docTitle").textContent = doc.title;
      const previewBtn = document.getElementById("btnPreviewOriginal");
      previewBtn.disabled = !doc.hasOriginal;
      previewBtn.title = doc.hasOriginal
        ? "打开原始文档"
        : "该知识源没有原件（工单沉淀生成）";
      /* 元数据行：真实接口里 size / effective / updated 可能为空，空值不显示，
         避免出现「— 生效」这类读不通的文案 */
      const metaBits = [FORMAT_META[doc.format].label];
      if (doc.size && doc.size !== "—") metaBits.push(doc.size);
      if (doc.effective && doc.effective !== "—") metaBits.push(doc.effective + " 生效");
      if (doc.updated && doc.updated !== "—") metaBits.push("上次解析 " + doc.updated);
      document.getElementById("docMeta").textContent = metaBits.join(" · ");

      document.getElementById("docTags").innerHTML = [
        doc.status === "active"
          ? '<span class="tag tag--green"><i class="dot dot--green"></i>已生效</span>'
          : '<span class="tag tag--gray"><i class="dot dot--gray"></i>已禁用</span>',
        doc.lifecycle === "legacy" ? `<span class="tag tag--amber">旧版 ${doc.version}</span>` : "",
        `<span class="tag tag--gray">型号 ${doc.model}</span>`,
        `<span class="tag tag--gray">批次 ${doc.batch}</span>`,
        `<span class="tag tag--gray">${doc.chunks.toLocaleString()} 个证据单元</span>`,
      ].filter(Boolean).join("");

      // 版本提醒只在同型号存在其它版本时出现
      const bar = document.getElementById("docVersionBar");
      const text = document.getElementById("docVersionText");
      const legacy = legacySibling();
      const current = currentSibling();
      if (doc.lifecycle === "legacy" && current) {
        text.textContent = `本手册为旧版 ${doc.version}，只服务「${doc.batch}」批次的设备；现行版为 ${current.version}。问答按工单的设备批次自动选版，命中不同版本时在 widget 并列展示，系统不替工程师选择。`;
        bar.hidden = false;
      } else if (legacy) {
        text.textContent = `本手册存在旧版 ${legacy.version}（适用 ${legacy.batch}）。问答时系统按工单的设备批次自动选版：${doc.batch} 检索本版，${legacy.batch} 检索旧版，两版冲突会在 widget 给出冲突提示。`;
        bar.hidden = false;
      } else {
        bar.hidden = true;
      }

      const chunkTotal = (window.MOCK.chunkTotals && window.MOCK.chunkTotals[doc.id] != null)
        ? window.MOCK.chunkTotals[doc.id]
        : doc.chunks;
      document.getElementById("chunkHeadNote").textContent = chunks.length
        ? `共 ${Number(chunkTotal).toLocaleString()} 条证据单元`
        : "该知识源暂无证据单元";
    }

    function flagOf(c) {
      if (c.flagBad) return '<span class="tag tag--red">疑似误切 · 建议禁用</span>';
      if (c.flagWarn) return '<span class="tag tag--amber">元数据待修正</span>';
      return "";
    }

    function renderList() {
      listEl.innerHTML = chunks.map((c) => `
        <div class="chunk-item ${c.id === selectedId ? "active" : ""} ${c.status === "disabled" ? "is-disabled" : ""}" data-id="${c.id}">
          <div class="chunk-item__loc">
            <svg viewBox="0 0 24 24"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/></svg>
            ${c.loc} <span style="margin-left:auto;color:#8a97a3;font-weight:400">${pageLinkHtml(c)}</span>
          </div>
          <div class="chunk-item__txt">${c.text.split("\n")[0]}</div>
        </div>`).join("");
    }

    /* 批次选项按数据生成（已知批次 + 当前值）：
       此前写死 4 个选项，遇到别的批次值 select 会静默落到第一项，点保存就写错了元数据。 */
    function batchOptions(current) {
      const cur = current === "—" ? "全部批次" : current;
      const known = ["全部批次", "B23", "B22 及以后", "B21 及以前"];
      const all = (known.indexOf(cur) < 0) ? known.concat([cur]) : known;
      return all.map((b) =>
        `<option value="${b}" ${b === cur ? "selected" : ""}>${b === "全部批次" ? "全部批次（不限）" : b}</option>`).join("");
    }

    function renderDetail() {
      const c = chunks.find((x) => x.id === selectedId);
      if (!c) return;
      detailEl.innerHTML = `
        <div class="detail__section">
          <div class="detail__label">
            <svg viewBox="0 0 24 24"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/></svg>
            证据原文（${c.loc}${pageNo(c) ? " · " + pageLinkHtml(c) : ""} · ID ${c.evidenceId}）
            <span style="margin-left:auto;display:flex;gap:6px">${flagOf(c)}</span>
          </div>
          <div class="evidence">${c.text.replace(/</g, "&lt;")}</div>
          <div class="hint">
            <svg viewBox="0 0 24 24"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>
            widget 回答中引用此单元时，引用卡标题与出处定位（${c.page}）必须与这里完全一致；引用存在性由后端程序校验，不靠提示词保证。
          </div>
        </div>

        <div class="detail__section">
          <div class="detail__label">
            <svg viewBox="0 0 24 24"><path d="M16 18 22 12l-6-6M8 6l-6 6 6 6"/></svg>
            检索元数据（决定这条证据在什么问题下被召回）
          </div>
          <div class="meta-grid">
            <div><label>适用设备型号</label><input id="mModel" value="${c.model}" /></div>
            <div>
              <label>适用批次</label>
              <select id="mBatch">
                ${batchOptions(c.batch)}
              </select>
            </div>
            <div><label>手册版本</label><input id="mVersion" value="${c.version}" /></div>
            <div><label>${c.provKind || "所属章节 / 页码"}</label><input id="mLoc" value="${[c.loc, c.page].filter((v) => v && v !== "—").join(" · ")}" /></div>
            <div style="grid-column:1 / -1">
              <label>证据 ID（系统生成，不可改）</label>
              <input value="${c.evidenceId}" disabled style="background:#f4f6f8;color:#8a97a3" />
            </div>
          </div>
          <div class="hint">
            <svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><path d="M12 16v-4M12 8h.01"/></svg>
            查询预处理得到的型号/批次未知时留空检索，不允许猜；因此批次标错（如把 B23 经验标成全部批次）会造成跨批次误召回，是本页最重要的治理动作。停用后该证据立即退出检索，但行与原文保留，可随时恢复。
          </div>
        </div>

        <div class="detail__section">
          <div class="detail__label">
            <svg viewBox="0 0 24 24"><path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/></svg>
            治理操作
          </div>
          <div class="detail__ops">
            <label class="switch">
              <input type="checkbox" id="mEnabled" ${c.status === "active" ? "checked" : ""} />
              <i></i><span id="mEnabledText">${c.status === "active" ? "启用中（参与检索）" : "已禁用（不参与检索）"}</span>
            </label>
            <span class="spacer"></span>
            <button class="btn btn--ghost" id="btnReset">还原修改</button>
            <button class="btn btn--primary" id="btnSave">
              <svg viewBox="0 0 24 24"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><path d="M17 21v-8H7v8M7 3v5h8"/></svg>
              保存修改
            </button>
          </div>
        </div>`;

      document.getElementById("mEnabled").addEventListener("change", (e) => {
        document.getElementById("mEnabledText").textContent =
          e.target.checked ? "启用中（参与检索）" : "已禁用（不参与检索）";
      });
      document.getElementById("btnSave").addEventListener("click", () => {
        if (!c) return;
        const modelEl = document.getElementById("mModel");
        const batchEl = document.getElementById("mBatch");
        const versionEl = document.getElementById("mVersion");
        const locEl = document.getElementById("mLoc");
        if (modelEl && modelEl.value.trim() !== c.model) {
          toast("设备型号归属知识源，请在「编辑元数据」中修改（切片级不支持）");
          return;
        }
        const patch = {
          status: document.getElementById("mEnabled").checked ? "active" : "disabled",
          sectionNo: (function () {
            const raw = (locEl.value.split("·")[0] || "").trim().replace(/^§/, "");
            return (!raw || raw === "未标注章节") ? null : raw;
          })(),
          batch: batchEl.value === "全部批次" ? null : batchEl.value,
          version: (function () {
            const text = versionEl.value.trim();
            return (!text || text === "—") ? null : text;
          })(),
        };
        const btn = document.getElementById("btnSave");
        btn.disabled = true;
        window.AdminAPI.patchChunk(c.id, patch)
          .then((updated) => {
            const mapped = window.AdminAPI.toPrototypeChunk(updated);
            const idx = chunks.findIndex((x) => x.id === mapped.id);
            if (idx >= 0) chunks[idx] = mapped;
            renderList();
            renderDetail();
            toast("已保存：切片元数据与启停用已更新（检索立即生效）");
          })
          .catch((ex) => {
            btn.disabled = false;
            if (ex && ex.status === 401) return;
            toast((ex && ex.message) || "保存失败，请稍后再试");
          });
      });
      document.getElementById("btnReset").addEventListener("click", () => {
        renderDetail();
        toast("已还原为上次保存的内容");
      });
    }

    listEl.addEventListener("click", (e) => {
      const pageLink = e.target.closest("[data-preview-page]");
      if (pageLink) {
        e.stopPropagation();
        openPreview(Number(pageLink.dataset.previewPage));
        return;
      }
      const item = e.target.closest(".chunk-item");
      if (!item) return;
      selectedId = item.dataset.id;
      renderList();
      renderDetail();
    });

    detailEl.addEventListener("click", (e) => {
      const pageLink = e.target.closest("[data-preview-page]");
      if (!pageLink) return;
      openPreview(Number(pageLink.dataset.previewPage));
    });

    document.getElementById("btnReparse").addEventListener("click", () => {
      const btn = document.getElementById("btnReparse");
      btn.disabled = true;
      window.AdminAPI.reparseDocument(doc.id)
        .then(() => {
          btn.disabled = false;
          toast("已重新排队解析：worker 会在下一轮重建切片与向量（进度见「解析任务」）");
        })
        .catch((ex) => {
          btn.disabled = false;
          if (ex && ex.status === 401) return;
          toast((ex && ex.message) || "重新解析失败，请稍后再试");
        });
    });

    /* 文档级元数据补录（FR-K1：批次 / 版本 / 生效日期可在上传后补录） */
    const metaModal = document.getElementById("metaModal");
    document.getElementById("btnEditMeta").addEventListener("click", () => {
      document.getElementById("eModel").value = doc.model;
      document.getElementById("eBatch").value = doc.batch;
      document.getElementById("eVersion").value = doc.version;
      document.getElementById("eEffective").value = doc.effective;
      metaModal.hidden = false;
    });
    document.getElementById("saveMeta").addEventListener("click", () => {
      const clean = (v) => {
        const text = String(v || "").trim();
        return text && text !== "—" ? text : null;
      };
      const patch = {
        model: clean(document.getElementById("eModel").value),
        batch: clean(document.getElementById("eBatch").value),
        version: clean(document.getElementById("eVersion").value),
        effective: clean(document.getElementById("eEffective").value),
      };
      const btn = document.getElementById("saveMeta");
      btn.disabled = true;
      window.AdminAPI.patchDocument(doc.id, patch)
        .then((updated) => {
          const mapped = window.AdminAPI.toPrototypeDocument(updated);
          Object.assign(doc, mapped);
          metaModal.hidden = true;
          renderBanner();
          toast("文档元数据已保存：批次/版本/生效期已更新");
        })
        .catch((ex) => {
          btn.disabled = false;
          if (ex && ex.status === 401) return;
          toast((ex && ex.message) || "保存失败，请稍后再试");
        });
    });

    renderBanner();
    if (!chunks.length) {
      // 空态：解析未完成或该知识源确实没有可入库的证据单元
      document.getElementById("chunkEmpty").hidden = false;
      detailEl.innerHTML = `<div class="empty">
        <svg viewBox="0 0 24 24"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/></svg>
        该知识源暂无证据单元：解析任务可能尚未完成，可在「解析任务」页查看进度。</div>`;
    } else {
      renderList();
      renderDetail();
    }
  }

  /* ---------------- 启动 ----------------
   * 知识源 / 解析任务 / 切片 三页在 app.js 内；P1/P2 页面各自一个文件（pages/*.js），
   * 注册到 window.AdminPages[page] 即可，由这里统一启动（保证 initModals 先执行）。
   */
  document.addEventListener("DOMContentLoaded", () => {
    // 主题缓存优先：theme-boot 已在首屏前恢复；这里不能用内置默认色覆盖它。
    // 没有缓存时（首次打开）才应用 MOCK 内置品牌；真实品牌由 layout 异步读取后覆盖。
    const cachedTheme = (() => {
      try { return !!window.localStorage.getItem("moqian_admin_theme"); } catch (e) { return false; }
    })();
    const brandCfg = (window.MOCK.systemConfig && window.MOCK.systemConfig.brand) || {};
    if (!cachedTheme) applyBrandTheme(brandCfg.primaryColor);

    initModals();
    /* 首屏数据加载失败（非 401）：明确提示，不静默渲染空表 */
    if (window.MOCK && window.MOCK.__loadError) {
      toast("数据加载失败：" + window.MOCK.__loadError);
    }
    const page = document.body.dataset.page;
    if (page === "login") { initLoginPage(); return; } // 登录页无侧栏
    if (page === "sources") initSourcesPage();
    if (page === "jobs") initJobsPage();
    if (page === "document") initDocumentPage();
    const bootPage = window.AdminPages && window.AdminPages[page];
    if (bootPage) bootPage();
  });
})();
