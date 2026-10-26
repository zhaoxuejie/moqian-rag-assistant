/* ============================================================
 * real-api.js — 后台真实后端适配层（替代早期原型的 mock 数据层）
 *
 * 为什么存在：后台的页面/样式/交互直接沿用已确认原型，
 * 页面脚本仍从 window.MOCK 读取数据；本文件在页面脚本之前运行，
 * 调真实接口并**把返回值映射成原型数据结构**，因此页面代码无需感知 API。
 *
 * 约定（与 app/serializers.py、contracts/openapi.yaml 一致）：
 *   POST /admin/auth/login            {account,password} → {token,expiresAt,user}
 *   GET  /admin/me                    → AdminUser
 *   GET  /admin/documents             ?q&category&status&lifecycle&page&pageSize&withStats
 *   GET  /admin/documents/{id}        → DocumentDetail
 *   GET  /admin/documents/{id}/chunks ?q&status&page&pageSize
 *   GET  /admin/jobs                  ?status&page&pageSize&withStats
 *   POST /admin/jobs/{id}/retry       → Job（仅 failed 可重试，否则 409）
 *   POST /admin/documents             multipart(file,category,title,deviceModel,batch,manualVersion)
 *
 * 登录态：JWT 存 localStorage（key=admin_token），用户信息 admin_user；
 * 未登录/401 一律回 login.html（前端跳转只是体验，接口本身已由后端鉴权）。
 *
 * 数据量假设：M0 阶段单页数据量小，列表一次取 pageSize=100 后在前端筛选/分页；
 * 超过 100 条时列表页会只展示前 100 条（已在 REBASE.md 记为已知限制）。
 * ============================================================ */
(function () {
  "use strict";

  var PAGE = (document.body && document.body.dataset.page) || "";

  /* 同源托管：静态站挂 /admin-app/，接口在 /admin/*。
     本地另开 http server 时可先设置 window.ADMIN_API_BASE 覆盖。 */
  var API_BASE = window.ADMIN_API_BASE || "/admin";
  API_BASE = String(API_BASE).replace(/\/+$/, "");

  var TOKEN_KEY = "admin_token";
  var USER_KEY = "admin_user";

  /* ---------------- 登录态 ---------------- */
  function storageGet(key) {
    try { return localStorage.getItem(key); } catch (e) { return null; }
  }
  function storageSet(key, value) {
    try { localStorage.setItem(key, value); } catch (e) { /* 隐私模式：登录态无法持久化 */ }
  }
  function storageDel(key) {
    try { localStorage.removeItem(key); } catch (e) { /* 同上 */ }
  }

  var auth = {
    token: function () { return storageGet(TOKEN_KEY) || ""; },
    user: function () {
      try {
        var raw = storageGet(USER_KEY);
        return raw ? JSON.parse(raw) : null;
      } catch (e) { return null; }
    },
    set: function (token, user) {
      storageSet(TOKEN_KEY, token);
      storageSet(USER_KEY, JSON.stringify(user || null));
    },
    clear: function () {
      storageDel(TOKEN_KEY);
      storageDel(USER_KEY);
    },
  };

  function currentFile() {
    var parts = String(location.pathname).split("/");
    return parts[parts.length - 1] || "index.html";
  }

  function toLogin() {
    if (currentFile() === "login.html") return;
    location.replace("login.html");
  }

  function handleUnauthorized() {
    auth.clear();
    toLogin();
  }

  /* ---------------- HTTP ---------------- */
  function apiUrl(path, query) {
    var url = API_BASE + path;
    if (query) {
      var parts = [];
      Object.keys(query).forEach(function (key) {
        var value = query[key];
        if (value === undefined || value === null || value === "") return;
        parts.push(encodeURIComponent(key) + "=" + encodeURIComponent(String(value)));
      });
      if (parts.length) url += "?" + parts.join("&");
    }
    return url;
  }

  function parseBody(xhr) {
    var text = xhr.responseText || "";
    if (!text) return null;
    try { return JSON.parse(text); } catch (e) { return text; }
  }

  function errorOf(xhr, data) {
    var message = "请求失败（HTTP " + xhr.status + "）";
    var code = "ERROR";
    if (data && typeof data === "object") {
      if (data.message) message = String(data.message);
      if (data.code) code = String(data.code);
    }
    return { status: xhr.status, code: code, message: message, data: data };
  }

  /* 同步请求：首屏数据必须在页面脚本执行前就绪（页面沿用原型的同步初始化）。
     这是唯一一处同步 XHR；交互动作全部走下面的异步 request。 */
  function requestSync(method, path, query) {
    var xhr = new XMLHttpRequest();
    xhr.open(method, apiUrl(path, query), false);
    var token = auth.token();
    if (token) xhr.setRequestHeader("Authorization", "Bearer " + token);
    try {
      xhr.send(null);
    } catch (e) {
      return {
        ok: false, status: 0,
        error: { status: 0, code: "NETWORK", message: "无法连接后端接口（" + API_BASE + "），请确认 app 服务已启动" },
      };
    }
    var data = parseBody(xhr);
    if (xhr.status === 401) {
      handleUnauthorized();
      return { ok: false, status: 401, unauthorized: true, error: errorOf(xhr, data) };
    }
    if (xhr.status < 200 || xhr.status >= 300) {
      return { ok: false, status: xhr.status, error: errorOf(xhr, data) };
    }
    return { ok: true, status: xhr.status, data: data };
  }

  function request(method, path, options) {
    options = options || {};
    return new Promise(function (resolve, reject) {
      var xhr = new XMLHttpRequest();
      xhr.open(method, apiUrl(path, options.query), true);
      var token = auth.token();
      if (token) xhr.setRequestHeader("Authorization", "Bearer " + token);
      if (options.json !== undefined) xhr.setRequestHeader("Content-Type", "application/json");
      xhr.onload = function () {
        var data = parseBody(xhr);
        if (xhr.status === 401) {
          handleUnauthorized();
          reject(errorOf(xhr, data));
          return;
        }
        if (xhr.status < 200 || xhr.status >= 300) {
          reject(errorOf(xhr, data));
          return;
        }
        resolve(data);
      };
      xhr.onerror = function () {
        reject({ status: 0, code: "NETWORK", message: "无法连接后端接口，请确认 app 服务已启动" });
      };
      if (options.formData) xhr.send(options.formData);
      else if (options.json !== undefined) xhr.send(JSON.stringify(options.json));
      else xhr.send(null);
    });
  }

  /* ---------------- 原型枚举（沿用早期原型的取值，保持稳定） ----------------
     后端目前只产出 manual/pdf；其余枚举保留，未知值动态注册，避免页面取 label 抛错。 */
  var CATEGORY_META = {
    manual: { label: "手册与文档" },
    faq: { label: "FAQ 表格" },
    table: { label: "数据表格" },
    ticket: { label: "工单数据" },
    drawing: { label: "图纸目录" },
  };
  var FORMAT_META = {
    pdf: { label: "PDF" },
    docx: { label: "Word" },
    txt: { label: "TXT" },
    md: { label: "Markdown" },
    xlsx: { label: "Excel" },
    excel: { label: "Excel" },
    csv: { label: "CSV" },
    ticketpkg: { label: "工单数据包" },
    drawingindex: { label: "图纸目录索引" },
  };
  var CATEGORY_FORMATS = {
    manual: ["pdf", "docx", "txt", "md"],
    faq: ["xlsx"],
    table: ["xlsx", "csv"],
    ticket: ["xlsx", "csv"],
    drawing: ["drawingindex"],
  };

  function ensureMeta(category, format) {
    if (category && !CATEGORY_META[category]) CATEGORY_META[category] = { label: category };
    if (format && !FORMAT_META[format]) FORMAT_META[format] = { label: String(format).toUpperCase() };
  }

  /* ---------------- 契约 → 原型数据结构 ----------------
     只做字段改名与空值兜底，不改变数值/文案口径（数字一律来自后端）。 */
  function docToPrototype(d) {
    ensureMeta(d.category, d.format);
    var lifecycle = d.lifecycle || "current";
    var archived = lifecycle === "archived";
    var disabled = lifecycle === "disabled" || d.status === "disabled";
    // 原型只认 current / legacy / disabled；superseded 映射为 legacy（旧版仍启用），
    // archived 是新增态（默认不在列表里，只有勾了「显示已归档」才会拿到）
    var protoLifecycle = archived
      ? "archived"
      : (disabled ? "disabled" : (lifecycle === "superseded" ? "legacy" : "current"));
    return {
      id: d.id,
      category: d.category,
      format: d.format,
      title: d.title || "（未命名知识源）",
      model: d.model || "—",
      batch: d.batch || "—",
      version: d.version || "—",
      effective: d.effective || "—",
      status: archived ? "archived" : (disabled ? "disabled" : "active"),
      lifecycle: protoLifecycle,
      chunks: Number(d.chunks || 0),
      size: d.size || "—",
      updated: d.updated || "—",
      uploader: d.uploader || "—",
      statusRaw: d.status || "",
      docCode: d.docCode || null,
      fileSha256: d.fileSha256 || null,
      hasOriginal: !!d.hasOriginal,
    };
  }

  /* 后端 job.stage 是中文阶段名，原型流程条需要 5 个阶段键；
     真实接口只知道任务状态，不知道细粒度阶段，因此按状态给一个诚实的最小映射。 */
  var JOB_STAGE_KEY = { queued: "upload", running: "parse", failed: "parse", done: "done", cancelled: "parse" };

  function jobToPrototype(j) {
    ensureMeta(j.category, j.format);
    var status = j.status || "queued";
    var note = j.note || "";
    return {
      id: j.id,
      docId: j.docId || null,
      docTitle: j.docTitle || (j.docId ? j.docId : "非文档任务"),
      category: j.category || null,
      format: j.format || null,
      stage: JOB_STAGE_KEY[status] || "upload",
      status: status,
      cancelRequested: !!j.cancelRequested,
      progress: Number(j.progress || 0),
      startedAt: j.startedAt || (status === "queued" ? "排队中" : "—"),
      finishedAt: j.finishedAt || null,
      note: status === "failed" ? "" : note,
      error: status === "failed" ? (note || "任务失败（后端未返回原因）") : "",
      attempts: Number(j.attempts || 0),
    };
  }

  function chunkToPrototype(c) {
    return {
      id: c.id,
      evidenceId: c.evidenceId || "",
      loc: c.loc || (c.sectionNo ? "§" + c.sectionNo : "未标注章节"),
      page: c.page || "—",
      provKind: c.provKind || "所属章节 / 页码",
      text: c.text || "",
      status: c.status === "disabled" ? "disabled" : "active",
      model: c.model || "—",
      batch: c.batch || "—",
      version: c.version || "—",
      sectionNo: c.sectionNo || null,
      tokens: c.tokens || null,
    };
  }

  /* ---------------- window.MOCK：保持原型读取形状 ---------------- */
  var MOCK = {
    CATEGORY_META: CATEGORY_META,
    FORMAT_META: FORMAT_META,
    CATEGORY_FORMATS: CATEGORY_FORMATS,
    documents: [],
    jobs: [],
    chunksByDoc: {},
    chunkTotals: {},
    docDetails: {},
    STATS: {},
    jobCounts: null,
    /* 未接入页面的占位数组：导航已隐藏这些页面，保留空数组避免布局脚本取值报错 */
    settlements: [],
    qaLogs: [],
    gaps: [],
    users: [],
    evalCases: [],
    evalRuns: [],
    dashboard: null,
    identity: {},
    /* 真实数据源没有"会话级演示持久化"：保留空实现，页面调用点无需分支 */
    save: function () {},
    reset: function () { auth.clear(); },
    /* 页面/验收脚本可据此确认当前不是 mock 数据 */
    __source: "real-api",
    systemConfig: {
      brand: {
        systemName: "知识助手管理台",
        productName: "售后知识助手",
        orgName: "陌阡科技",
        primaryColor: "#0f7a52",
        logo: "default",
        logoFileName: "",
      },
      /* 后端 POST /admin/auth/captcha 当前恒返回 {enabled:false}：登录页不画假验证码 */
      security: { captchaEnabled: false },
      /* 知识自测（/admin/preview/ask）未开放，入口隐藏，见 preview.js */
      preview: { enabled: false },
    },
  };
  window.MOCK = MOCK;
  window.AdminAuth = auth;

  /* ---------------- 各页面首屏数据 ---------------- */
  function fail(message) {
    MOCK.__loadError = message || "数据加载失败";
  }

  function bootSources() {
    var res = requestSync("GET", "/documents", { page: 1, pageSize: 100, withStats: "true" });
    if (res.unauthorized) return;
    if (!res.ok) { fail(res.error && res.error.message); return; }
    var data = res.data || {};
    MOCK.documents = (data.items || []).map(docToPrototype);
    var stats = data.stats || {};
    /* 任务徽标 / 进行中数量：只取 counts，列表本身在任务页拉 */
    var jobRes = requestSync("GET", "/jobs", { page: 1, pageSize: 1, withStats: "true" });
    var counts = jobRes.ok && jobRes.data && jobRes.data.stats ? jobRes.data.stats.counts : null;
    MOCK.jobCounts = counts;
    MOCK.STATS = {
      docTotal: data.pagination ? data.pagination.total : MOCK.documents.length,
      totalChunks: stats.totalChunks != null ? stats.totalChunks : null,
      chunksAdded7d: stats.chunksAdded7d != null ? stats.chunksAdded7d : null,
      byCategory: stats.byCategory || null,
      currentVersionCount: stats.currentVersionCount != null ? stats.currentVersionCount : null,
      jobCounts: counts,
    };
  }

  function bootJobs() {
    var res = requestSync("GET", "/jobs", { page: 1, pageSize: 100, withStats: "true" });
    if (res.unauthorized) return;
    if (!res.ok) { fail(res.error && res.error.message); return; }
    var data = res.data || {};
    MOCK.jobs = (data.items || []).map(jobToPrototype);
    var counts = data.stats ? data.stats.counts : null;
    MOCK.jobCounts = counts;
    MOCK.jobTotal = data.pagination ? data.pagination.total : MOCK.jobs.length;
    MOCK.STATS = { jobCounts: counts };
  }

  function bootDocument() {
    var list = requestSync("GET", "/documents", { page: 1, pageSize: 100 });
    if (list.unauthorized) return;
    if (!list.ok) { fail(list.error && list.error.message); return; }
    MOCK.documents = ((list.data && list.data.items) || []).map(docToPrototype);
    if (!MOCK.documents.length) return;

    var wanted = null;
    try { wanted = new URLSearchParams(location.search).get("id"); } catch (e) { wanted = null; }
    var doc = null;
    for (var i = 0; i < MOCK.documents.length; i++) {
      if (MOCK.documents[i].id === wanted) { doc = MOCK.documents[i]; break; }
    }
    if (!doc) doc = MOCK.documents[0];

    var detail = requestSync("GET", "/documents/" + encodeURIComponent(doc.id));
    if (detail.ok && detail.data) {
      MOCK.docDetails[doc.id] = detail.data;
      if (detail.data.chunks != null) doc.chunks = Number(detail.data.chunks);
      if (detail.data.status) doc.statusRaw = detail.data.status;
      doc.hasOriginal = !!detail.data.hasOriginal;
    }

    var chunks = requestSync(
      "GET",
      "/documents/" + encodeURIComponent(doc.id) + "/chunks",
      { page: 1, pageSize: 100 }
    );
    if (chunks.ok && chunks.data) {
      MOCK.chunksByDoc[doc.id] = (chunks.data.items || []).map(chunkToPrototype);
      MOCK.chunkTotals[doc.id] = chunks.data.pagination
        ? chunks.data.pagination.total
        : MOCK.chunksByDoc[doc.id].length;
    }
  }

  try {
    if (PAGE && PAGE !== "login") {
      if (!auth.token()) {
        toLogin();
      } else {
        var me = requestSync("GET", "/me");
        if (me.ok) {
          auth.set(auth.token(), me.data);
          if (PAGE === "sources") bootSources();
          else if (PAGE === "jobs") bootJobs();
          else if (PAGE === "document") bootDocument();
        }
      }
    }
  } catch (e) {
    fail(e && e.message);
  }

  /* ---------------- 暴露给页面脚本 ---------------- */
  window.AdminAPI = {
    source: "real-api",
    auth: auth,
    request: request,
    requestSync: requestSync,
    apiUrl: apiUrl,
    toPrototypeDocument: docToPrototype,
    toPrototypeJob: jobToPrototype,
    toPrototypeChunk: chunkToPrototype,
    login: function (account, password, captcha) {
      var body = { account: account, password: password };
      if (captcha) {
        body.captchaId = captcha.captchaId;
        body.captchaCode = captcha.captchaCode;
      }
      return request("POST", "/auth/login", { json: body });
    },
    captcha: function () {
      return request("POST", "/auth/captcha", { json: {} });
    },
    publicBrand: function () { return request("GET", "/auth/brand"); },
    me: function () { return request("GET", "/me"); },
    previewAsk: function (body) {
      return request("POST", "/preview/ask", { json: body });
    },
    retryJob: function (id) {
      return request("POST", "/jobs/" + encodeURIComponent(id) + "/retry");
    },
    cancelJob: function (id) {
      return request("PATCH", "/jobs/" + encodeURIComponent(id), { json: { action: "cancel" } });
    },
    enableDocument: function (id, enabled) {
      return request("POST", "/documents/" + encodeURIComponent(id) + "/enable", {
        json: { enabled: !!enabled },
      });
    },
    /** 归档（不是物理删除）：移出列表与检索池，切片与原件保留，可 restore。 */
    archiveDocument: function (id) {
      return request("POST", "/documents/" + encodeURIComponent(id) + "/archive");
    },
    restoreDocument: function (id) {
      return request("POST", "/documents/" + encodeURIComponent(id) + "/restore");
    },
    patchDocument: function (id, patch) {
      return request("PATCH", "/documents/" + encodeURIComponent(id), { json: patch });
    },
    reparseDocument: function (id) {
      return request("POST", "/documents/" + encodeURIComponent(id) + "/reparse");
    },
    patchChunk: function (id, patch) {
      return request("PATCH", "/chunks/" + encodeURIComponent(id), { json: patch });
    },
    documentVersions: function (id) {
      return request("GET", "/documents/" + encodeURIComponent(id) + "/versions");
    },
    /** 原文预览描述：kind=pdf / text / unsupported（PDF 本身走 documentFile）。 */
    documentPreview: function (id) {
      return request("GET", "/documents/" + encodeURIComponent(id) + "/preview");
    },
    /** 读取原件为 Blob：预览与下载共用；iframe 不能自带 Bearer，所以先 fetch。 */
    documentFile: function (id) {
      var token = auth.token();
      return fetch(apiUrl("/documents/" + encodeURIComponent(id) + "/file"), {
        headers: token ? { Authorization: "Bearer " + token } : {},
      }).then(function (res) {
        if (!res.ok) {
          return res.text().then(function (text) {
            var message = "原件读取失败（HTTP " + res.status + "）";
            try {
              var data = JSON.parse(text);
              if (data && data.message) message = data.message;
            } catch (e) { /* 非 JSON 响应保留默认提示 */ }
            throw { status: res.status, message: message };
          });
        }
        return res.blob();
      });
    },
    uploadDocument: function (formData) {
      return request("POST", "/documents", { formData: formData });
    },
    reloadJobs: function () {
      return request("GET", "/jobs", { query: { page: 1, pageSize: 100, withStats: "true" } });
    },
    reloadDocuments: function (options) {
      var query = { page: 1, pageSize: 100, withStats: "true" };
      /* 归档资料默认不出现在列表里：勾「显示已归档」时显式带 lifecycle=archived 再取一遍 */
      if (options && options.lifecycle) query.lifecycle = options.lifecycle;
      return request("GET", "/documents", { query: query });
    },
    reloadChunks: function (documentId) {
      return request("GET", "/documents/" + encodeURIComponent(documentId) + "/chunks", {
        query: { page: 1, pageSize: 100 },
      });
    },
  };
})();
