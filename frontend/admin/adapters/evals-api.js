/* ============================================================
 * evals-api.js — 「评测面板 + 主管驾驶舱」数据适配层（真实后端）
 *
 * 分层约定（D17，与 real-api.js 一致）：
 *   - 本文件只做 HTTP 与字段搬运，**不碰 DOM**；
 *   - 页面控制器（controllers/eval.js、controllers/dashboard.js）只读本适配层返回的结构化数据，
 *     不自己 fetch；
 *   - 接口形状以 contracts/openapi.yaml 为准（已 camelCase，不做二次改名）。
 *
 * 端点：
 *   GET  /admin/evals                              → EvalList
 *   POST /admin/evals/run                          → EvalRun（202，前端轮询单次详情）
 *   GET  /admin/evals/{runId}                      → EvalRunDetail
 *   GET  /admin/stats/overview?week=               → StatsOverview
 *   GET  /admin/stats/north-star?week=             → NorthStar
 *   GET  /admin/stats/engineers?week=&page=&pageSize= → EngineerStatPage
 *   GET  /admin/stats/recognizability?week=        → RecognizabilityStats
 *   GET  /admin/reports/weekly?week=               → WeeklyReport（JSON sections；Markdown 由控制器生成）
 *
 * 依赖：real-api.js 先加载（提供 window.AdminAPI.request 与登录态/401 处理）。
 * ============================================================ */
(function () {
  "use strict";

  var API = window.AdminAPI;

  function request(method, path, options) {
    if (!API || !API.request) {
      return Promise.reject({ status: 0, code: "NO_API", message: "接口适配层未加载（real-api.js）" });
    }
    return API.request(method, path, options);
  }

  function withWeek(week, extra) {
    var query = Object.assign({}, extra || {});
    if (week !== undefined && week !== null && String(week) !== "") query.week = String(week);
    return query;
  }

  window.AdminEvalsApi = {
    /** 评测用例集 + 历史批次。 */
    listEvals: function (options) {
      options = options || {};
      return request("GET", "/evals", {
        query: { page: options.page || 1, pageSize: options.pageSize || 100 },
      });
    },
    /** 触发一次评测（缺省跑全量；caseIds / category 任选）。 */
    runEval: function (payload) {
      return request("POST", "/evals/run", { json: payload || {} });
    },
    /** 仓库默认用例集：清空当前表并恢复 evals/cases.yaml。 */
    resetCases: function () {
      return request("POST", "/evals/cases/reset", { json: {} });
    },
    /** 上传 CSV / Excel 用例（≤2MB）；后端逐行校验后幂等写入。 */
    importCasesFile: function (file) {
      var form = new FormData();
      form.append("file", file);
      return request("POST", "/evals/cases/import-file", { formData: form });
    },
    /** 下载用例模板（二进制，需带登录态；返回 Blob）。 */
    caseTemplate: function (format) {
      var token = window.AdminAuth && window.AdminAuth.token
        ? window.AdminAuth.token()
        : "";
      return fetch(API.apiUrl("/evals/cases/template", { format: format || "csv" }), {
        headers: token ? { Authorization: "Bearer " + token } : {},
      }).then(function (res) {
        if (!res.ok) {
          return res.text().then(function (text) {
            var message = "模板下载失败（HTTP " + res.status + "）";
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
    /** 单次批次详情（指标 + 逐例结果），用于轮询状态。 */
    getEvalRun: function (runId) {
      return request("GET", "/evals/" + encodeURIComponent(runId));
    },
    /** 驾驶舱概览（KPI / 近 6 周趋势 / 高频问题 / 定位时长分布）。 */
    statsOverview: function (week) {
      return request("GET", "/stats/overview", { query: withWeek(week) });
    },
    /** 北极星（分子/分母 + 工程师级与会话级双轨）。 */
    northStar: function (week) {
      return request("GET", "/stats/north-star", { query: withWeek(week) });
    },
    /** 按工程师统计（分页）。 */
    statsEngineers: function (week, page, pageSize) {
      return request("GET", "/stats/engineers", {
        query: withWeek(week, { page: page || 1, pageSize: pageSize || 100 }),
      });
    },
    /** 可识别率与身份来源分布。 */
    recognizability: function (week) {
      return request("GET", "/stats/recognizability", { query: withWeek(week) });
    },
    /** 周报八节（JSON；Markdown 由 dashboard 控制器按 sections 生成下载）。 */
    weeklyReport: function (week) {
      return request("GET", "/reports/weekly", { query: withWeek(week) });
    },
  };
})();
