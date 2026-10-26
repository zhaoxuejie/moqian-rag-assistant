/* ============================================================
 * logs-api.js — 问答日志 / 知识缺口的接口适配层
 *
 * 分层约定（frontend/admin 零构建）：
 *   - 本文件只做 HTTP 调用与「契约 → 页面数据结构」映射，不碰 DOM；
 *   - 页面控制器（controllers/logs.js、gaps.js）只操作 DOM 与交互；
 *   - 依赖 real-api.js 暴露的 window.AdminAPI.request（统一附加 JWT、401 回登录页）。
 *
 * 契约：contracts/openapi.yaml
 *   GET   /admin/messages                    ?page&pageSize&from&to&outcome&resolved&identity&withStats
 *   GET   /admin/messages/{messageId}/chain
 *   GET   /admin/gaps                        ?status&page&pageSize&withStats
 *   PATCH /admin/gaps/{gapId}                {status, coveredBy?, note?}
 *   GET   /admin/documents                   （「标记覆盖」下拉的资料来源，只读）
 *
 * 口径：数值/枚举一律来自后端，前端只改字段名与展示格式，不补造数据。
 * ============================================================ */
(function () {
  "use strict";

  const API_BASE = window.AdminAPI;
  if (!API_BASE) return;

  const OUTCOME_LABEL = {
    answered: "正常回答",
    gap: "证据不足拒答",
    safe: "安全拦截",
    error: "服务失败",
  };
  const RESOLVED_LABEL = { resolved: "已解决", unresolved: "没解决", none: "未标记" };
  const IDENTITY_LABEL = {
    gateway_sso: "网关 SSO",
    host_injected: "宿主注入",
    password_login: "密码登录",
    anonymous: "未识别",
  };
  const GAP_STATUS_LABEL = { open: "未覆盖", covered: "已覆盖", ignored: "已忽略" };

  function pad(value) {
    return String(value).padStart(2, "0");
  }

  /* 后端返回 ISO 时间；页面沿用原型的「日期 + 时分」展示，原始值保留 raw* 供排序/核对 */
  function fmtTime(iso) {
    if (!iso) return "—";
    const date = new Date(iso);
    if (isNaN(date.getTime())) return String(iso);
    return date.getFullYear() + "-" + pad(date.getMonth() + 1) + "-" + pad(date.getDate())
      + " " + pad(date.getHours()) + ":" + pad(date.getMinutes());
  }

  function fmtGapTime(iso) {
    if (!iso) return "—";
    const date = new Date(iso);
    if (isNaN(date.getTime())) return String(iso);
    return pad(date.getMonth() + 1) + "-" + pad(date.getDate()) + " "
      + pad(date.getHours()) + ":" + pad(date.getMinutes());
  }

  function identityOf(raw) {
    const source = (raw && raw.source) || "anonymous";
    return {
      source: source,
      claimed: (raw && raw.claimed) || null,
      engineer: (raw && raw.engineer) || null,
      label: IDENTITY_LABEL[source] || source,
    };
  }

  function structuredOf(raw) {
    const value = raw || {};
    return {
      symptom: value.symptom || null,
      model: value.model || null,
      batch: value.batch || null,
      code: value.code || null,
      missing: Array.isArray(value.missing) ? value.missing.slice() : [],
      batchOrigin: value.batchOrigin || null,
    };
  }

  function hitsOf(raw) {
    return (raw || []).map(function (hit) {
      return {
        evidenceId: hit.evidenceId || null,
        loc: hit.loc || null,
        kind: hit.kind || null,
        score: hit.score === null || hit.score === undefined ? null : Number(hit.score),
      };
    });
  }

  function strippedOf(raw) {
    return (raw || []).map(function (item) {
      return {
        evidenceId: item.evidenceId || null,
        reason: item.reason || "",
      };
    });
  }

  function toLog(item) {
    return {
      id: item.id,
      sessionId: item.sessionId || null,
      time: fmtTime(item.time),
      rawTime: item.time,
      user: item.user || "未识别",
      ticket: item.ticket || null,
      identity: identityOf(item.identity),
      question: item.question || "",
      structured: structuredOf(item.structured),
      hits: hitsOf(item.hits),
      citations: Number(item.citations || 0),
      stripped: strippedOf(item.stripped),
      outcome: item.outcome || "answered",
      resolved: item.resolved || "none",
      resolvedInSec: item.resolvedInSec === null || item.resolvedInSec === undefined
        ? null
        : Number(item.resolvedInSec),
      handoff: !!item.handoff,
      channel: item.channel || "widget",
    };
  }

  function toChainRound(round) {
    return {
      question: round.question || "",
      answerSummary: round.answerSummary || "",
      outcome: round.outcome || "answered",
      gateReason: round.gateReason || null,
      citations: Array.isArray(round.citations) ? round.citations : [],
      stripped: Array.isArray(round.stripped) ? round.stripped.slice() : [],
      askFields: Array.isArray(round.askFields) ? round.askFields.slice() : [],
      elapsedMs: Number(round.elapsedMs || 0),
    };
  }

  function toGap(item) {
    return {
      id: item.id,
      model: item.model || null,
      symptom: item.symptom || "",
      count: Number(item.count || 0),
      firstAt: fmtGapTime(item.firstAt),
      lastAt: fmtGapTime(item.lastAt),
      rawFirstAt: item.firstAt || null,
      rawLastAt: item.lastAt || null,
      status: item.status || "open",
      coveredBy: item.coveredBy || null,
      relatedMessages: Array.isArray(item.relatedMessages) ? item.relatedMessages.slice() : [],
    };
  }

  function toDocumentOption(item) {
    return {
      id: item.id,
      title: item.title || "（未命名知识源）",
      version: item.version || "—",
      category: item.category || "manual",
    };
  }

  function listMessages(params) {
    const query = {
      page: params.page || 1,
      pageSize: params.pageSize || 20,
      from: params.from,
      to: params.to,
      outcome: params.outcome,
      resolved: params.resolved,
      identity: params.identity,
      withStats: params.withStats ? "true" : undefined,
    };
    return API_BASE.request("GET", "/messages", { query: query }).then(function (data) {
      return {
        items: (data.items || []).map(toLog),
        pagination: data.pagination || { page: 1, pageSize: 20, total: 0 },
        stats: data.stats || null,
      };
    });
  }

  function getChain(messageId) {
    return API_BASE.request(
      "GET", "/messages/" + encodeURIComponent(messageId) + "/chain"
    ).then(function (data) {
      return {
        message: toLog(data.message || {}),
        rounds: (data.rounds || []).map(toChainRound),
      };
    });
  }

  function listGaps(params) {
    const query = {
      status: params.status,
      page: params.page || 1,
      pageSize: params.pageSize || 100,
      withStats: params.withStats ? "true" : undefined,
    };
    return API_BASE.request("GET", "/gaps", { query: query }).then(function (data) {
      return {
        items: (data.items || []).map(toGap),
        pagination: data.pagination || { page: 1, pageSize: 100, total: 0 },
        stats: data.stats || null,
      };
    });
  }

  function patchGap(gapId, patch) {
    return API_BASE.request(
      "PATCH", "/gaps/" + encodeURIComponent(gapId), { json: patch }
    ).then(toGap);
  }

  /* 「标记覆盖」要求指明是哪份知识补上的：只列可能承载技术内容的资料类别 */
  function listDocuments() {
    return API_BASE.request("GET", "/documents", {
      query: { page: 1, pageSize: 100 },
    }).then(function (data) {
      return (data.items || [])
        .filter(function (item) {
          const category = item.category || "manual";
          return category === "manual" || category === "faq" || category === "ticket";
        })
        .map(toDocumentOption);
    });
  }

  window.LogsAPI = {
    source: "real-api",
    OUTCOME_LABEL: OUTCOME_LABEL,
    RESOLVED_LABEL: RESOLVED_LABEL,
    IDENTITY_LABEL: IDENTITY_LABEL,
    GAP_STATUS_LABEL: GAP_STATUS_LABEL,
    toLog: toLog,
    toGap: toGap,
    listMessages: listMessages,
    getChain: getChain,
    listGaps: listGaps,
    patchGap: patchGap,
    listDocuments: listDocuments,
  };
})();
