/* ============================================================
 * my-qa.js — 我的问答（个人域只读视图，从 account.js 拆出）
 *
 * 数据源：window.UsersApi.boot("my-qa")（GET /admin/me/messages）
 * 本文件只做渲染与分页交互，不发请求、不造数据。
 * ============================================================ */
(function () {
  "use strict";
  window.AdminPages = window.AdminPages || {};

  window.AdminPages["my-qa"] = function () {
    var UI = window.UI;
    var api = window.UsersApi;
    if (!UI || !api) return;

    var boot = api.boot("my-qa");
    var esc = UI.esc;
    var toast = UI.toast;
    var $ = function (id) { return document.getElementById(id); };

    var OUTCOME = {
      answered: { label: "正常回答", cls: "tag--green" },
      gap: { label: "证据不足拒答", cls: "tag--red" },
      safe: { label: "安全拦截", cls: "tag--amber" },
      error: { label: "服务失败", cls: "tag--gray" },
    };
    var RESOLVED = {
      resolved: { label: "已解决", cls: "tag--green" },
      unresolved: { label: "没解决", cls: "tag--red" },
      none: { label: "未标记", cls: "tag--gray" },
    };

    var PAGE_SIZE = 10;
    var selectedQa = null;
    var messages = boot.messages || [];
    var pagination = boot.messagesPagination || {};

    function fmtSec(seconds) {
      if (typeof seconds !== "number") return "—";
      if (seconds < 60) return seconds + " 秒";
      var minutes = Math.floor(seconds / 60);
      return minutes + " 分 " + String(seconds % 60).padStart(2, "0") + " 秒";
    }
    function outcomeMeta(value) { return OUTCOME[value] || OUTCOME.error; }
    function resolvedMeta(value) { return RESOLVED[value] || RESOLVED.none; }

    function renderQa() {
      if (selectedQa && !messages.some(function (item) { return item.id === selectedQa; })) {
        selectedQa = null;
      }
      $("qaEmpty").hidden = messages.length > 0;
      $("qaRows").innerHTML = messages.map(function (item) {
        var outcome = outcomeMeta(item.outcome);
        var resolved = resolvedMeta(item.resolved);
        return '<tr data-qa="' + esc(item.id) + '"'
          + ' class="' + (item.id === selectedQa ? "row--hl" : "") + '" style="cursor:pointer">'
          + '<td style="white-space:nowrap;color:#52606d">' + esc(item.time || "—") + "</td>"
          + "<td>" + esc(item.question) + "</td>"
          + '<td><span class="tag ' + outcome.cls + '">' + outcome.label + "</span></td>"
          + '<td class="num">' + item.citations + "</td>"
          + '<td><span class="tag ' + resolved.cls + '">' + resolved.label + "</span></td>"
          + "</tr>";
      }).join("");
      renderQaDetail();
    }

    /* 本轮流程：只读，只讲"我问了什么 → 系统怎么查 → 依据 → 结果"。
       不给引用剥离明细与全量相关度——那是管理员「问答日志」的复盘视角。 */
    function renderQaDetail() {
      var card = $("qaDetail");
      var box = $("qaDetailBody");
      var message = messages.filter(function (item) { return item.id === selectedQa; })[0];
      if (!message) {
        card.hidden = true;
        box.innerHTML = "";
        return;
      }
      card.hidden = false;

      var steps = [];
      var identity = message.identity || {};
      steps.push({
        title: "① 我问了什么",
        body: esc(message.question)
          + '<br/><span style="color:#8a97a3">时间：' + esc(message.time || "—")
          + " · 会话：" + esc(message.sessionId || "—")
          + (message.ticket ? " · 工单 " + esc(message.ticket) : " · 未带入工单上下文")
          + " · 身份来源：" + esc(identity.source || "anonymous")
          + (identity.engineer ? "（在册工号 " + esc(identity.engineer) + "）" : "")
          + "</span>",
      });

      var structured = message.structured || null;
      if (structured) {
        var fields = [
          ["现象", structured.symptom],
          ["型号", structured.model],
          ["批次", structured.batch],
          ["报警码", structured.code],
        ];
        steps.push({
          title: "② 系统整理的检索条件",
          body: fields.map(function (pair) {
            return '<span class="mono">' + pair[0] + "</span>"
              + (pair[1]
                ? esc(pair[1])
                : '<span style="color:#c0392b">未提供 · 不参与过滤（系统不猜）</span>');
          }).join("<br/>"),
        });
      } else {
        steps.push({
          title: "② 系统整理的检索条件",
          body: '<span style="color:#8a97a3">本轮未提取到结构化条件，按全库检索</span>',
        });
      }

      var hits = message.hits || [];
      steps.push({
        title: "③ 命中的依据（" + hits.length + " 条）",
        body: hits.length
          ? hits.slice(0, 5).map(function (hit) {
            return '<span class="mono">' + esc(hit.evidenceId || "—") + "</span>"
              + esc(hit.loc || "")
              + (hit.kind ? '<span style="color:#8a97a3"> · ' + esc(hit.kind) + "</span>" : "")
              + (typeof hit.score === "number"
                ? '<span style="color:#8a97a3"> · 相关度 ' + hit.score.toFixed(2) + "</span>"
                : "");
          }).join("<br/>")
            + (hits.length > 5
              ? '<br/><span style="color:#8a97a3">…另有 ' + (hits.length - 5) + " 条</span>"
              : "")
          : '<span style="color:#c0392b">零命中：这一轮没有找到能支撑回答的资料</span>',
        bad: !hits.length,
      });

      var outcome = outcomeMeta(message.outcome);
      var resolved = resolvedMeta(message.resolved);
      var result = "结果：" + outcome.label + " · 引用 " + message.citations + " 条"
        + "<br/>我的标记：" + resolved.label
        + (message.resolvedInSec != null ? " · 首次定位耗时 " + fmtSec(message.resolvedInSec) : "");
      if (message.handoff) result += "<br/>本会话已转人工";
      result += '<br/><span style="color:#8a97a3">渠道：'
        + (message.channel === "admin_preview" ? "管理员自测" : "工程师端 Widget") + "</span>";
      steps.push({ title: "④ 结果与我的反馈", body: result, bad: message.outcome !== "answered" });

      box.innerHTML = '<div class="chain">' + steps.map(function (step) {
        return '<div class="chain__step ' + (step.bad ? "is-bad" : "") + '">'
          + '<div class="chain__title">' + step.title + "</div>"
          + '<div class="chain__body">' + step.body + "</div>"
          + "</div>";
      }).join("") + "</div>";
    }

    function renderQaPager() {
      UI.fixedPager(
        $("qaPager"),
        Number(pagination.total || messages.length),
        Number(pagination.page || 1),
        PAGE_SIZE,
        loadQa
      );
    }

    function loadQa(page) {
      return api.reloadMessages({ page: page, pageSize: PAGE_SIZE })
        .then(function (rows) {
          messages = rows || [];
          pagination = api.data.messagesPagination || {};
          selectedQa = null;
          renderQa();
          renderQaPager();
        })
        .catch(function (ex) {
          if (ex && ex.status === 401) return;
          toast((ex && ex.message) || "问答记录加载失败");
        });
    }

    $("qaRows").addEventListener("click", function (event) {
      var row = event.target.closest("tr[data-qa]");
      if (!row) return;
      var id = row.getAttribute("data-qa");
      selectedQa = id === selectedQa ? null : id; // 再点一次收起
      renderQa();
    });

    renderQa();
    renderQaPager();
    if (boot.error) toast("数据加载失败：" + boot.error);
  };
})();
