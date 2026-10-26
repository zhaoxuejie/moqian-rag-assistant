/* ============================================================
 * logs.js — 问答日志页控制器（DOM / 交互）
 *
 * 数据来源：adapters/logs-api.js（契约 → 页面结构），本文件不直接 fetch。
 * 页面结构、类名与文案沿用早期原型；真实数据下的差异：
 *   - 列表由后端分页（page/pageSize），筛结果类型走 /admin/messages?outcome=；
 *   - 「仅看发生过引用剥离的轮次」只过滤**当前页**（接口没有该筛选参数）；
 *   - 被剥离的具体证据 ID 未落库，链路第 ⑤ 步展示后端给出的剥离说明。
 * 回答侧的三条硬规则在这里可见：
 *   ① 结构化条件里未知字段留空（不是猜一个值）
 *   ② 引用只能来自本轮证据集，非法引用被程序剥离
 *   ③ 证据不足 / 安全话题分别走拒答与拦截，语义不同、分开统计
 * ============================================================ */
(function () {
  "use strict";
  window.AdminPages = window.AdminPages || {};

  function initLogs() {
    const UI = window.UI || {};
    const LogsAPI = window.LogsAPI;
    const tbody = document.getElementById("logRows");
    const emptyEl = document.getElementById("logEmpty");
    const detailEl = document.getElementById("logDetail");
    const pagerEl = document.getElementById("logPager");
    const rateNote = document.getElementById("rateNote");
    if (!tbody || !emptyEl || !detailEl || !LogsAPI) return;

    const esc = UI.esc || function (value) { return String(value == null ? "" : value); };
    const OUTCOME = LogsAPI.OUTCOME_LABEL;
    const RESOLVED = LogsAPI.RESOLVED_LABEL;
    const OUTCOME_CLS = {
      answered: "tag--green",
      gap: "tag--red",
      safe: "tag--amber",
      error: "tag--gray",
    };
    const RESOLVED_CLS = {
      resolved: "tag--green",
      unresolved: "tag--red",
      none: "tag--gray",
    };

    const LOGS_PAGE_SIZE_KEY = "moqian_logs_page_size";

    const state = {
      page: 1,
      pageSize: UI.readPageSize ? UI.readPageSize(LOGS_PAGE_SIZE_KEY, 20) : 20,
      outcome: "all",
      onlyStripped: false,
      selectedId: null,
    };
    let items = [];
    let total = 0;
    let stats = null;
    let chainSeq = 0;
    let loadSeq = 0;

    const put = function (key, value) {
      const el = document.querySelector('[data-stat="' + key + '"]');
      if (el) el.textContent = value;
    };

    function fmtSec(sec) {
      if (sec === null || sec === undefined) return "—";
      if (sec < 60) return sec + " 秒";
      const minutes = Math.floor(sec / 60);
      return minutes + " 分 " + String(sec % 60).padStart(2, "0") + " 秒";
    }

    /* ---------------- 列表 ---------------- */

    function showListError(message) {
      items = [];
      total = 0;
      stats = null;
      tbody.innerHTML = "";
      emptyEl.hidden = false;
      emptyEl.innerHTML = '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/>'
        + '<path d="M12 8v4M12 16h.01"/></svg>问答日志加载失败：' + esc(message);
      detailEl.innerHTML = '<div class="empty">数据加载失败，无法展示链路</div>';
      if (pagerEl) pagerEl.innerHTML = "";
      renderStats();
    }

    function renderTable() {
      const list = state.onlyStripped
        ? items.filter(function (item) { return item.stripped.length > 0; })
        : items;
      emptyEl.hidden = list.length > 0;
      if (!list.length) {
        tbody.innerHTML = "";
        if (!state.onlyStripped) emptyEl.textContent = "当前筛选下没有问答记录";
        return;
      }

      tbody.innerHTML = list.map(function (item) {
        const strippedNote = item.stripped.length
          ? ' · <span style="color:#c0392b">有引用被整条剥离</span>'
          : "";
        return '<tr data-id="' + esc(item.id) + '" class="'
          + (item.id === state.selectedId ? "row--hl" : "") + '">'
          + '<td style="white-space:nowrap;color:#52606d">' + esc(item.time) + "</td>"
          + "<td>" + esc(item.user) + "</td>"
          + "<td>" + identityCell(item) + "</td>"
          + '<td><div style="font-weight:600">' + esc(item.question) + "</div>"
          + '<div class="sub">' + (item.ticket
            ? "工单 " + esc(item.ticket)
            : "无宿主上下文") + strippedNote + "</div></td>"
          + '<td><span class="tag ' + (OUTCOME_CLS[item.outcome] || "tag--gray") + '">'
          + esc(OUTCOME[item.outcome] || item.outcome) + "</span></td>"
          + '<td class="num">' + item.citations + "</td>"
          + '<td class="num">' + fmtSec(item.resolvedInSec) + "</td>"
          + '<td><span class="tag ' + (RESOLVED_CLS[item.resolved] || "tag--gray") + '">'
          + esc(RESOLVED[item.resolved] || item.resolved) + "</span></td>"
          + "</tr>";
      }).join("");
    }

    /* 身份来源标签（PRD §2.3）：engineer 为空即未识别，只计入会话级指标 */
    function identityCell(item) {
      const identity = item.identity;
      if (!identity.engineer) {
        const why = identity.claimed
          ? "声明的工号 " + identity.claimed + " 未开通，已记入「待开通工号」"
          : "本次未取得身份";
        return '<span class="idtag idtag--amber" title="' + esc(why)
          + '；不计入按人统计">未识别</span>'
          + (identity.claimed ? '<div class="sub">声明 ' + esc(identity.claimed) + "</div>" : "");
      }
      return '<span class="idtag">' + esc(identity.label) + "</span>";
    }

    function renderStats() {
      const gap = items.filter(function (item) { return item.outcome === "gap"; }).length;
      const safe = items.filter(function (item) { return item.outcome === "safe"; }).length;
      const strippedTurns = items.filter(function (item) {
        return item.stripped.length > 0;
      }).length;

      put("logs", items.length);
      put("logsNote", "共 " + total + " 轮 · 本页 " + items.length + " 轮");
      put("gap", gap);
      put("gapNote", gap ? "本页已自动归入「知识缺口」清单" : "本页无证据不足拒答");
      put("safe", safe);
      put("safeNote", safe ? "不生成操作步骤，仅引用安规原文" : "本页无安全拦截");
      put("stripped", strippedTurns);
      put("strippedNote", strippedTurns ? "机制生效，非故障" : "本页无引用剥离");
      renderIdentityRate();
    }

    /* 可识别率（PRD §1.4）：与北极星同级的数据质量指标，低于阈值时按人指标只做参考 */
    function renderIdentityRate() {
      const counted = stats && typeof stats.rounds === "number"
        ? stats.rounds
        : items.filter(function (item) { return item.channel !== "admin_preview"; }).length;
      const identified = stats && typeof stats.identifiableRounds === "number"
        ? stats.identifiableRounds
        : items.filter(function (item) {
          return item.channel !== "admin_preview" && item.identity.engineer;
        }).length;
      const rate = counted ? identified / counted : 0;
      const low = rate < 0.8;
      const pct = Math.round(rate * 100) + "%";

      if (rateNote) {
        rateNote.textContent = "本页 " + counted + " 轮 · 可识别 " + identified + " 轮 · 可识别率 "
          + pct + (low ? "（低于阈值，按人指标仅供参考）" : "")
          + " ｜ 点击任意一行查看完整链路"
          + (state.onlyStripped ? "（当前仅展示发生过引用剥离的轮次）" : "");
      }

      const bar = document.getElementById("idBar");
      if (!bar) return;
      bar.innerHTML = '<div class="warnbar ' + (low ? "warnbar--amber" : "warnbar--green") + '">'
        + '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/>'
        + '<path d="M12 8v4M12 16h.01"/></svg>'
        + "<span>身份可识别率 <b>" + pct + "</b>（阈值 80%）：可识别 <b>" + identified
        + "</b> 轮进入按人统计，未识别 <b>" + Math.max(0, counted - identified)
        + "</b> 轮仍计入会话级指标（拒答率 / 转人工率 / 引用与标记）。"
        + (low ? "偏低通常说明身份接入没配好，不是没人用——先查宿主有没有传当前操作者工号。" : "")
        + "</span></div>";
    }

    function renderPager() {
      if (!pagerEl || !UI.pager) return;
      UI.pager(pagerEl, total, state.page, state.pageSize, function (page, pageSize) {
        state.page = page;
        state.pageSize = pageSize;
        state.selectedId = null;
        load();
      }, LOGS_PAGE_SIZE_KEY);
    }

    function render() {
      renderStats();
      renderTable();
      renderPager();
    }

    function load() {
      const seq = ++loadSeq;
      tbody.innerHTML = '<tr><td colspan="8" style="text-align:center;color:#8a97a3">'
        + "加载中…</td></tr>";
      emptyEl.hidden = true;
      const params = {
        page: state.page,
        pageSize: state.pageSize,
        withStats: true,
      };
      if (state.outcome !== "all") params.outcome = state.outcome;

      LogsAPI.listMessages(params).then(function (data) {
        if (seq !== loadSeq) return;
        items = data.items;
        total = data.pagination.total;
        stats = data.stats;
        if (!items.some(function (item) { return item.id === state.selectedId; })) {
          state.selectedId = items.length ? items[0].id : null;
        }
        render();
        loadChain();
      }).catch(function (ex) {
        if (seq !== loadSeq) return;
        if (ex && ex.status === 401) return;
        showListError((ex && ex.message) || "请稍后重试");
        if (window.toast) toast("问答日志加载失败：" + ((ex && ex.message) || "请稍后重试"));
      });
    }

    /* ---------------- 链路详情 ---------------- */

    function roundOf(message, rounds) {
      const sameQuestion = rounds.filter(function (round) {
        return round.question === message.question;
      });
      const matched = sameQuestion.filter(function (round) {
        return round.outcome === message.outcome;
      });
      return matched.length ? matched[matched.length - 1]
        : (sameQuestion.length ? sameQuestion[sameQuestion.length - 1] : null);
    }

    function chainHtml(message, rounds) {
      const round = roundOf(message, rounds);
      const steps = [];
      const identity = message.identity;
      const idLabel = identity.label;
      const idText = identity.engineer
        ? esc(message.user) + "（工号 " + esc(identity.engineer) + "）· 来源：" + esc(idLabel)
        : "未识别 · 来源：" + esc(idLabel)
          + (identity.claimed
            ? " · 声明工号 " + esc(identity.claimed) + " 未开通（已记入待开通清单）"
            : "")
          + " · 不计入按人统计";

      // ① 原始问题
      steps.push({
        title: "① 原始问题",
        body: esc(message.question) + "<br/><span style='color:#8a97a3'>来源："
          + (message.ticket
            ? "工单 " + esc(message.ticket) + " 注入上下文"
            : "无宿主上下文，按全库检索")
          + "</span><br/><span style='color:#8a97a3'>身份：" + idText + "</span>",
      });

      // ② 结构化条件：未知字段必须留空
      const structured = message.structured;
      const fields = [
        ["现象", structured.symptom],
        ["型号", structured.model],
        ["批次", structured.batch],
        ["报警码", structured.code],
      ];
      steps.push({
        title: "② 结构化检索条件",
        body: fields.map(function (pair) {
          const key = pair[0];
          const value = pair[1];
          return value
            ? '<span class="mono">' + key + "</span>" + esc(value)
            : '<span class="mono">' + key
              + "</span><span style='color:#c0392b'>未提供 · 不参与过滤（系统不猜）</span>";
        }).join("<br/>"),
      });

      // ③ 命中证据
      const hits = message.hits || [];
      steps.push({
        title: "③ 命中证据（" + hits.length + " 条）",
        body: hits.length
          ? hits.map(function (hit) {
            const score = hit.score === null || hit.score === undefined
              ? "相关度未落库"
              : "相关度 " + hit.score.toFixed(2);
            return '<span class="mono">' + esc(hit.evidenceId || "—") + "</span>"
              + esc(hit.loc || "出处未落库") + " <span style='color:#8a97a3'>· "
              + esc(score) + (hit.kind === "ticket" ? " · 工单来源" : "") + "</span>";
          }).join("<br/>")
          : "<span style='color:#c0392b'>零命中：全库没有支撑本轮回答的证据</span>",
        muted: !hits.length,
      });

      // ④ 回答 / 拒答 / 拦截 / 失败
      const gateReason = round ? round.gateReason : null;
      const summary = round && round.answerSummary ? esc(round.answerSummary) : "";
      if (message.outcome === "answered") {
        steps.push({
          title: "④ 生成回答并校验引用",
          body: "输出排查步骤，附引用 " + message.citations + " 条；逐条校验通过后发出。"
            + (summary ? "<br/><span style='color:#8a97a3'>回答摘要：" + summary + "</span>" : ""),
        });
      } else if (message.outcome === "gap") {
        steps.push({
          title: gateReason === "citation_stripped"
            ? "④ 引用校验后无依据，降级为拒答"
            : "④ Evidence Gate 判定证据不足",
          body: summary || "本轮没有可核验的引用，已按规则拒答。",
          bad: true,
        });
      } else if (message.outcome === "safe") {
        steps.push({
          title: "④ 安全规则拦截",
          body: summary || "命中安全话题：不生成任何操作步骤，只提示联系厂家并引用安规原文。",
          bad: true,
        });
      } else {
        steps.push({
          title: "④ 服务失败降级",
          body: summary || "服务暂时不可用，本轮已记入日志并允许重试。",
          bad: true,
        });
      }

      // ⑤ 非法引用剥离（机制证据，单独一条）
      if (message.stripped.length) {
        steps.push({
          title: "⑤ 引用校验：引用被整条剥离",
          body: message.stripped.map(function (item) {
            return (item.evidenceId
              ? '<span class="mono">' + esc(item.evidenceId) + "</span>"
              : "") + esc(item.reason);
          }).join("<br/>"),
          bad: true,
        });
      }

      // ⑥ 结果与反馈
      steps.push({
        title: "⑥ 结果与反馈",
        body: "结果：" + esc(OUTCOME[message.outcome] || message.outcome)
          + " · 解决标记：" + esc(RESOLVED[message.resolved] || message.resolved)
          + " · 首次定位耗时：" + fmtSec(message.resolvedInSec)
          + " · 转人工：" + (message.handoff ? "是" : "否"),
        muted: message.outcome !== "answered",
      });

      return '<div class="chain">' + steps.map(function (step) {
        return '<div class="chain__step ' + (step.bad ? "is-bad " : "")
          + (step.muted ? "is-muted" : "") + '">'
          + '<div class="chain__title">' + step.title + "</div>"
          + '<div class="chain__body">' + step.body + "</div></div>";
      }).join("") + "</div>";
    }

    /* 工单完成 → 一键沉淀（FR-K5 第二条进水管）：
       工单没有独立表，host_context.ticketNo 是锚点；后端按工单号聚合已解决轮次，
       只产出「待确认」候选，仍需在知识沉淀页人工确认才入库。 */
    function settleTicket(message, btn) {
      const API = window.AdminAPI;
      if (!API || typeof API.request !== "function") return;
      const toast = UI.toast || function () {};
      const original = btn.innerHTML;
      btn.disabled = true;
      btn.textContent = "正在生成候选…";
      API.request("POST", "/settlements/from-ticket", {
        json: { sessionId: message.sessionId },
      })
        .then(function (body) {
          const title = body && body.title ? "《" + body.title + "》" : "";
          if (body && body.created === false) {
            toast("该工单已有候选" + title + "，未重复创建；可到「知识沉淀」审核");
          } else {
            toast("已生成待确认候选" + title + "：人工确认后才入库");
          }
          if (window.AdminLayout && window.AdminLayout.refreshBadges) {
            window.AdminLayout.refreshBadges();
          }
          btn.disabled = false;
          btn.innerHTML = original;
        })
        .catch(function (err) {
          if (err && err.status === 401) return; // real-api 已跳登录
          btn.disabled = false;
          btn.innerHTML = original;
          toast((err && err.message) || "生成候选失败，请稍后再试");
        });
    }

    function renderDetail(chain) {
      const message = chain.message;
      const channelTag = message.channel === "admin_preview"
        ? '<span class="tag tag--gray">管理自测 · 不计入指标</span>'
        : "";
      /* 只有带工单号的真实会话才有「一键沉淀」；管理自测不进沉淀队列。 */
      const canSettle = !!message.ticket && message.channel !== "admin_preview";
      const settleBtn = canSettle
        ? '<button class="btn btn--ghost btn--sm" id="settleTicket" '
          + 'title="按工单号聚合已解决轮次，生成待确认候选（人工确认后才入库）">'
          + '<svg viewBox="0 0 24 24"><path d="m19 21-7-4-7 4V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2v16z"/></svg>'
          + "工单已完成 · 一键沉淀</button>"
        : "";
      detailEl.innerHTML = '<div class="card__head">'
        + "<h2>完整链路 · " + esc(message.id) + "</h2>"
        + '<span class="card__head spacer"></span>'
        + settleBtn
        + '<span class="tag ' + (OUTCOME_CLS[message.outcome] || "tag--gray") + '">'
        + esc(OUTCOME[message.outcome] || message.outcome) + "</span>"
        + channelTag
        + '<span style="font-size:11.5px;color:#8a97a3;margin-left:8px">本会话 '
        + chain.rounds.length + " 轮</span>"
        + '</div><div class="detail__section">'
        + chainHtml(message, chain.rounds) + "</div>";
      if (canSettle) {
        const btn = document.getElementById("settleTicket");
        if (btn) btn.addEventListener("click", function () { settleTicket(message, btn); });
      }
    }

    function loadChain() {
      if (!state.selectedId) {
        detailEl.innerHTML = '<div class="empty">选择一行查看完整链路</div>';
        return;
      }
      const seq = ++chainSeq;
      detailEl.innerHTML = '<div class="empty">加载链路中…</div>';
      LogsAPI.getChain(state.selectedId).then(function (chain) {
        if (seq !== chainSeq) return;
        renderDetail(chain);
      }).catch(function (ex) {
        if (seq !== chainSeq) return;
        if (ex && ex.status === 401) return;
        detailEl.innerHTML = '<div class="empty">链路加载失败：'
          + esc((ex && ex.message) || "请稍后重试") + "</div>";
      });
    }

    /* ---------------- 交互 ---------------- */

    tbody.addEventListener("click", function (event) {
      const row = event.target.closest("tr[data-id]");
      if (!row) return;
      state.selectedId = row.dataset.id;
      renderTable();
      loadChain();
    });

    const outcomeTabs = document.getElementById("outcomeTabs");
    if (outcomeTabs) {
      outcomeTabs.addEventListener("click", function (event) {
        const btn = event.target.closest("button[data-out]");
        if (!btn) return;
        state.outcome = btn.dataset.out;
        state.page = 1;
        state.selectedId = null;
        outcomeTabs.querySelectorAll("button").forEach(function (item) {
          item.classList.toggle("on", item === btn);
        });
        load();
      });
    }

    const strippedToggle = document.getElementById("onlyStripped");
    if (strippedToggle) {
      strippedToggle.addEventListener("change", function (event) {
        state.onlyStripped = event.target.checked;
        renderTable();
        renderIdentityRate();
      });
    }

    load();
  }

  window.AdminPages.logs = initLogs;
})();
