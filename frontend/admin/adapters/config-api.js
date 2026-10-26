/* ============================================================
 * config-api.js — 系统配置页适配层（契约 ↔ 页面视图模型）
 *
 * 职责边界（与 real-api.js / 页面控制器一致）：
 *   - 本文件只做 HTTP 与字段映射/校验，**不碰 DOM**；
 *   - 控制器（controllers/config.js）只读写 DOM，**不直接 fetch**；
 *   - 页面结构沿用早期原型，数据一律来自真实接口。
 *
 * 接口（contracts/openapi.yaml）：
 *   GET  /admin/config            → SystemConfig + runtime / effect / savedGroups（观测字段）
 *   PUT  /admin/config            → 同上（部分更新）
 *   POST /admin/config/test-llm   → LlmTestResult
 *   PUT  /admin/theme             → Theme
 *
 * 配置值 vs 生效值：接口返回的组是"配置值"（已保存值覆盖运行时值），
 * `runtime` 才是进程内真实生效值，`effect` 标注每组的生效方式。页面必须两者都展示，
 * 不能把"已保存"读成"已生效"（后端 docstring 有详细口径）。
 * ============================================================ */
(function () {
  "use strict";

  var HEX = /^#[0-9a-fA-F]{6}$/;
  var GROUP_NAMES = [
    "llm", "embedding", "reranker", "retrieval", "widget",
    "safety", "security", "preview", "demo", "brand",
  ];

  function request(method, path, body) {
    var api = window.AdminAPI;
    if (!api || typeof api.request !== "function") {
      return Promise.reject({
        code: "NO_API_LAYER",
        message: "真实接口适配层未加载（../adapters/real-api.js）",
      });
    }
    var options = {};
    if (body !== undefined) options.json = body;
    return api.request(method, path, options);
  }

  function str(value, fallback) {
    if (value === null || value === undefined) return fallback;
    return String(value);
  }

  function num(value, fallback) {
    var parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : fallback;
  }

  function arr(value) {
    return Array.isArray(value) ? value : [];
  }

  function obj(value) {
    return value && typeof value === "object" ? value : {};
  }

  /* ---------------- 契约 → 页面视图模型 ---------------- */
  function toFormModel(config) {
    var c = obj(config);
    var llm = obj(c.llm);
    var embedding = obj(c.embedding);
    var reranker = obj(c.reranker);
    var retrieval = obj(c.retrieval);
    var widget = obj(c.widget);
    var safety = obj(c.safety);
    var security = obj(c.security);
    var preview = obj(c.preview);
    var demo = obj(c.demo);
    var brand = obj(c.brand);
    var effect = obj(c.effect);
    return {
      llm: {
        baseUrl: str(llm.baseUrl, ""),
        model: str(llm.model, ""),
        timeoutSec: num(llm.timeoutSec, 60),
        apiKeySet: !!llm.apiKeySet,
        apiKeyMasked: llm.apiKeyMasked ? String(llm.apiKeyMasked) : null,
      },
      embedding: {
        baseUrl: str(embedding.baseUrl, ""),
        model: str(embedding.model, ""),
        dim: num(embedding.dim, 1024),
        provider: str(embedding.provider, ""),
      },
      reranker: {
        baseUrl: str(reranker.baseUrl, ""),
        model: str(reranker.model, ""),
        provider: str(reranker.provider, ""),
      },
      retrieval: {
        recallPerRoute: num(retrieval.recallPerRoute, 20),
        fusion: str(retrieval.fusion, "RRF"),
        topK: num(retrieval.topK, 12),
        evidenceThreshold: num(retrieval.evidenceThreshold, 0.35),
        recallPerRouteBm25: num(retrieval.recallPerRouteBm25, null),
        rrfK: num(retrieval.rrfK, null),
        evidenceMinCoverage: num(retrieval.evidenceMinCoverage, null),
      },
      widget: {
        welcome: str(widget.welcome, ""),
        samples: arr(widget.samples).map(function (item) { return String(item); }),
        handoffText: str(widget.handoffText, ""),
        brandColor: str(widget.brandColor, "#0f7a52"),
      },
      safety: {
        words: arr(safety.words).map(function (word) { return String(word); }),
      },
      security: { captchaEnabled: !!security.captchaEnabled },
      preview: { enabled: !!preview.enabled },
      /* 演示总开关：enabled=是否开放；envEnabled=部署是否启用了演示（DEMO_ENABLED） */
      demo: {
        enabled: demo.enabled !== false,
        envEnabled: !!(obj(c.runtime).demo && obj(c.runtime).demo.envEnabled),
      },
      brand: {
        systemName: str(brand.systemName, ""),
        productName: str(brand.productName, ""),
        orgName: str(brand.orgName, ""),
        primaryColor: str(brand.primaryColor, "#0f7a52"),
        logo: str(brand.logo, "default"),
        logoFileName: str(brand.logoFileName, ""),
      },
      /* 观测字段：配置值之外的"真实生效值 / 生效方式 / 说明"，页面原样展示 */
      runtime: obj(c.runtime),
      effect: effect,
      notes: arr(effect.notes || c.notes).map(function (note) { return String(note); }),
      savedGroups: arr(c.savedGroups).map(function (name) { return String(name); }),
    };
  }

  /* ---------------- 校验：错误信息用中文给到页面，不在服务端 422 后才提示 ---------------- */
  function fail(message) {
    var error = new Error(message);
    error.userMessage = message;
    throw error;
  }

  function requiredText(value, label) {
    var text = String(value === null || value === undefined ? "" : value).trim();
    if (!text) fail(label + "不能为空");
    return text;
  }

  function boundedInt(value, label, min, max) {
    var parsed = Number(value);
    if (!Number.isFinite(parsed) || Math.floor(parsed) !== parsed) fail(label + "必须是整数");
    if (parsed < min || parsed > max) fail(label + "需在 " + min + " ~ " + max + " 之间");
    return parsed;
  }

  function boundedNumber(value, label, min, max) {
    var parsed = Number(value);
    if (!Number.isFinite(parsed)) fail(label + "必须是数字");
    if (parsed < min || parsed > max) fail(label + "需在 " + min + " ~ " + max + " 之间");
    return parsed;
  }

  function color(value, label) {
    var text = String(value || "").trim();
    if (!HEX.test(text)) fail(label + "必须是 #RRGGBB 形式（例如 #0f7a52）");
    return text;
  }

  /* 重排 provider：none=只用 RRF；tencent=腾讯云 AI Search；llama_cpp=本地服务（预留）。
     开关页只写 none / 保留既有非 none 值，这里再守一道白名单，避免拼错静默启用。 */
  var RERANKER_PROVIDERS = ["none", "tencent", "llama_cpp"];

  /* 视图模型（表单草稿）→ PUT /admin/config 请求体。只提交改动过的组由调用方决定。 */
  function buildPatch(draft) {
    var d = obj(draft);
    var llm = obj(d.llm);
    var embedding = obj(d.embedding);
    var reranker = obj(d.reranker);
    var retrieval = obj(d.retrieval);
    var widget = obj(d.widget);
    var safety = obj(d.safety);
    var security = obj(d.security);
    var preview = obj(d.preview);
    var demo = obj(d.demo);
    var brand = obj(d.brand);
    var rrkProvider = String(reranker.provider || "none").trim().toLowerCase();
    if (RERANKER_PROVIDERS.indexOf(rrkProvider) < 0) {
      fail("重排 provider 不支持：" + rrkProvider);
    }

    var words = arr(safety.words)
      .map(function (word) { return String(word).trim(); })
      .filter(function (word) { return !!word; });
    var deduped = [];
    words.forEach(function (word) { if (deduped.indexOf(word) < 0) deduped.push(word); });

    return {
      llm: {
        baseUrl: requiredText(llm.baseUrl, "LLM 服务地址"),
        model: requiredText(llm.model, "模型名"),
        timeoutSec: boundedInt(llm.timeoutSec, "LLM 超时（秒）", 5, 600),
      },
      embedding: {
        baseUrl: String(embedding.baseUrl || "").trim(),
        model: requiredText(embedding.model, "embedding 模型"),
        dim: boundedInt(embedding.dim, "embedding 维度", 1, 16384),
      },
      reranker: {
        baseUrl: String(reranker.baseUrl || "").trim(),
        model: requiredText(reranker.model, "重排模型"),
        provider: rrkProvider,
      },
      retrieval: {
        recallPerRoute: boundedInt(retrieval.recallPerRoute, "每路召回条数", 1, 100),
        fusion: requiredText(retrieval.fusion, "融合方式"),
        topK: boundedInt(retrieval.topK, "重排后取用条数", 1, 50),
        evidenceThreshold: boundedNumber(retrieval.evidenceThreshold, "证据门槛阈值", 0, 1),
      },
      widget: {
        welcome: requiredText(widget.welcome, "欢迎语"),
        samples: arr(widget.samples)
          .map(function (item) { return String(item).trim(); })
          .filter(function (item) { return !!item; })
          .slice(0, 3),
        handoffText: requiredText(widget.handoffText, "转人工文案"),
        brandColor: color(widget.brandColor, "助手浮层品牌色"),
      },
      safety: { words: deduped },
      security: { captchaEnabled: !!security.captchaEnabled },
      preview: { enabled: !!preview.enabled },
      demo: { enabled: demo.enabled !== false },
      brand: {
        systemName: requiredText(brand.systemName, "系统名称"),
        productName: String(brand.productName || "").trim(),
        orgName: requiredText(brand.orgName, "机构名"),
        primaryColor: color(brand.primaryColor, "后台主色"),
        logo: String(brand.logo || "default").trim() || "default",
        logoFileName: String(brand.logoFileName || "").trim(),
      },
    };
  }

  /* 品牌主题写入口（PUT /admin/theme）：与 config.brand 同一份数据 */
  function buildThemePatch(brand) {
    var b = obj(brand);
    var patch = {
      systemName: requiredText(b.systemName, "系统名称"),
      orgName: requiredText(b.orgName, "机构名"),
      primaryColor: color(b.primaryColor, "后台主色"),
    };
    var productName = String(b.productName || "").trim();
    if (productName) patch.productName = productName;
    var logo = String(b.logo || "").trim();
    if (logo) patch.logo = logo;
    patch.logoFileName = String(b.logoFileName || "").trim();
    return patch;
  }

  window.AdminConfigAPI = {
    source: "config-api",
    groups: GROUP_NAMES,
    load: function () { return request("GET", "/config"); },
    save: function (patch) { return request("PUT", "/config", patch); },
    saveTheme: function (patch) { return request("PUT", "/theme", patch); },
    testLlm: function () { return request("POST", "/config/test-llm", undefined); },
    toFormModel: toFormModel,
    buildPatch: buildPatch,
    buildThemePatch: buildThemePatch,
    validateColor: color,
    isHex: function (value) { return HEX.test(String(value || "")); },
  };
})();
