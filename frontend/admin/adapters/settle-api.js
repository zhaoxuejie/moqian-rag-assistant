/* ============================================================
 * settle-api.js — 「知识沉淀」真实接口适配层
 *
 * 职责（与 real-api.js 同一约定）：
 *   1. 把契约字段（Settlement / SettlementDecision / SettlementPage）转成页面直接可用的形状；
 *   2. 封装 GET /admin/settlements 与 confirm / reject 三个真实请求；
 *   3. **不碰 DOM**：页面渲染、事件绑定全部在 controllers/settle.js。
 *
 * 依赖：real-api.js 暴露的 window.AdminAPI.request（异步）/ requestSync（首屏备用）。
 * 本文件不写任何模拟数据：没有后端数据时列表就是空的。
 * ============================================================ */
(function () {
  "use strict";

  var API = window.AdminAPI;
  var BASE = "/settlements";

  function text(value) {
    return value === undefined || value === null ? "" : String(value);
  }

  function nullable(value) {
    return value === undefined || value === null || value === "" ? null : String(value);
  }

  function pad2(value) {
    return String(value).padStart(2, "0");
  }

  /* 时间统一按浏览器本地时区展示：
     契约里的 submittedAt / reviewLog.at 是 ISO 8601（带时区偏移），
     直接截字符串会把 UTC 时间当成本地时间（会出现"审核记录与提交时间差 8 小时"）。
     历史数据里可能还有 "YYYY-MM-DD HH:MM" 的展示串，按本地时间解析，不再二次换算。 */
  function timeLabel(value) {
    var raw = text(value);
    if (!raw) return "";
    var parsed = new Date(raw.indexOf("T") >= 0 ? raw : raw.replace(" ", "T"));
    if (isNaN(parsed.getTime())) return raw;
    return parsed.getFullYear() + "-" + pad2(parsed.getMonth() + 1) + "-" + pad2(parsed.getDate())
      + " " + pad2(parsed.getHours()) + ":" + pad2(parsed.getMinutes());
  }

  /* 列表沿用原型 "10-16 16:40" 的短格式。 */
  function submittedAtLabel(value) {
    var label = timeLabel(value);
    if (!label) return "—";
    return label.replace(/^\d{4}-/, "");
  }

  function normalizeContext(rows) {
    return (rows || []).map(function (row) {
      row = row || {};
      return { k: text(row.k), v: text(row.v) };
    });
  }

  function normalizeReviewLog(rows) {
    return (rows || []).map(function (row) {
      row = row || {};
      return {
        at: timeLabel(row.at),
        by: text(row.by),
        // 契约枚举：confirmed | rejected | edited
        action: text(row.action),
        note: text(row.note),
      };
    });
  }

  function normalizeSettlement(raw) {
    raw = raw || {};
    return {
      id: text(raw.id),
      channel: raw.channel || "manual",
      status: raw.status || "pending",
      title: text(raw.title),
      source: nullable(raw.source),
      model: nullable(raw.model),
      batch: nullable(raw.batch),
      version: nullable(raw.version),
      submittedBy: nullable(raw.submittedBy),
      submittedAt: nullable(raw.submittedAt),
      submittedAtLabel: submittedAtLabel(raw.submittedAt),
      loc: nullable(raw.loc),
      text: text(raw.text),
      context: normalizeContext(raw.context),
      reviewLog: normalizeReviewLog(raw.reviewLog),
    };
  }

  function normalizePage(data) {
    data = data || {};
    var pagination = data.pagination || {};
    return {
      items: (data.items || []).map(normalizeSettlement),
      pagination: {
        page: Number(pagination.page || 1),
        pageSize: Number(pagination.pageSize || 0),
        total: Number(pagination.total || 0),
      },
      stats: data.stats || null,
    };
  }

  /* ---------------- 真实请求 ---------------- */
  function list(query) {
    return API.request("GET", BASE, { query: query }).then(normalizePage);
  }

  function confirm(id, decision) {
    return API.request(
      "POST",
      BASE + "/" + encodeURIComponent(id) + "/confirm",
      { json: decision || {} }
    ).then(normalizeSettlement);
  }

  function reject(id, decision) {
    return API.request(
      "POST",
      BASE + "/" + encodeURIComponent(id) + "/reject",
      { json: decision || {} }
    ).then(normalizeSettlement);
  }

  window.AdminSettleAPI = {
    source: "real-api",
    list: list,
    confirm: confirm,
    reject: reject,
    normalizeSettlement: normalizeSettlement,
  };
})();
