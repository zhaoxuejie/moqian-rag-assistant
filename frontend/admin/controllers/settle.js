/* ============================================================
 * settle.js — 知识沉淀审核（PRD FR-K5 / 纪律 8）
 *
 * 状态机：待确认 → 已入库 / 已驳回；两条通道（工单 / 收藏 / 手工）共用同一张队列。
 * 数据全部来自真实接口（adapters/settle-api.js）：
 *   GET  /admin/settlements              列表 + 状态计数
 *   POST /admin/settlements/{id}/confirm 确认入库（生成证据单元）
 *   POST /admin/settlements/{id}/reject  驳回（理由写入审核记录）
 *
 * 本文件只做 DOM 渲染与交互，不直接发请求、不放任何模拟数据。
 * ============================================================ */
(function () {
  "use strict";
  window.AdminPages = window.AdminPages || {};

  window.AdminPages.settle = function () {
    var UI = window.UI;
    var API = window.AdminSettleAPI;
    var listEl = document.getElementById("settleList");
    var detailEl = document.getElementById("settleDetail");
    var emptyEl = document.getElementById("settleEmpty");
    var noteEl = document.getElementById("listNote");
    var rejectModal = document.getElementById("rejectModal");
    var rejectNoteEl = document.getElementById("rejectNote");
    var confirmRejectBtn = document.getElementById("confirmReject");

    if (!UI || !API || !listEl || !detailEl) return;

    var PAGE_SIZE = 100;

    var CHANNEL = {
      ticket: { label: "工单沉淀", cls: "tag--blue" },
      favorite: { label: "对话收藏", cls: "tag--amber" },
      manual: { label: "手工沉淀", cls: "tag--gray" },
    };
    var CH_ICON = {
      ticket: '<svg viewBox="0 0 24 24"><path d="M2 9a3 3 0 0 1 0 6v2a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-2a3 3 0 0 1 0-6V7a2 2 0 0 0-2-2H4a2 2 0 0 0-2 2z"/><path d="M13 5v2M13 11v2M13 17v2"/></svg>',
      favorite: '<svg viewBox="0 0 24 24"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>',
      manual: '<svg viewBox="0 0 24 24"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/><path d="M16 13H8M16 17H8"/></svg>',
    };
    var CH_DOT = { ticket: "dot--amber", favorite: "dot--green", manual: "dot--gray" };
    var STATUS = {
      pending: { label: "待确认", cls: "tag--amber" },
      confirmed: { label: "已入库", cls: "tag--green" },
      rejected: { label: "已驳回", cls: "tag--gray" },
    };
    var REVIEW_ACTION = {
      confirmed: { label: "入库", bad: false },
      rejected: { label: "驳回", bad: true },
      edited: { label: "修正正文", bad: false },
    };

    var state = {
      statusFilter: "all",
      keyword: "",
      items: [],
      stats: null,
      total: 0,
      selectedId: null,
      pendingReject: null,
      loading: false,
      loadError: "",
      seq: 0,
    };

    function put(key, value) {
      var el = document.querySelector('[data-stat="' + key + '"]');
      if (el) el.textContent = value;
    }

    function channelOf(channel) {
      return CHANNEL[channel] || { label: channel || "未知通道", cls: "tag--gray" };
    }

    function dash(value) {
      return value === undefined || value === null || value === "" ? "—" : value;
    }

    function shortTitle(title) {
      var text = String(title || "候选条目");
      return text.length > 14 ? text.slice(0, 14) + "…" : text;
    }

    /* ---------------- 数据加载 ---------------- */
    function queryForList() {
      var query = { page: 1, pageSize: PAGE_SIZE, withStats: "true" };
      if (state.statusFilter !== "all") query.status = state.statusFilter;
      return query;
    }

    function load(keepId) {
      var seq = ++state.seq;
      state.loading = true;
      state.loadError = "";
      render();

      API.list(queryForList())
        .then(function (page) {
          if (seq !== state.seq) return;
          state.loading = false;
          state.items = page.items;
          state.stats = page.stats;
          state.total = page.pagination.total;
          state.selectedId = resolveSelection(keepId);
          render();
        })
        .catch(function (err) {
          if (seq !== state.seq) return;
          state.loading = false;
          if (err && err.status === 401) return; // real-api 已跳登录
          state.loadError = (err && err.message) || "数据加载失败";
          UI.toast("沉淀数据加载失败：" + state.loadError);
          render();
        });
    }

    function resolveSelection(preferredId) {
      if (preferredId && state.items.some(function (item) { return item.id === preferredId; })) {
        return preferredId;
      }
      if (state.items.some(function (item) { return item.id === state.selectedId; })) {
        return state.selectedId;
      }
      return state.items.length ? state.items[0].id : null;
    }

    /* ---------------- 统计卡 ---------------- */
    function renderStats() {
      var stats = state.stats;
      var pending = stats ? Number(stats.pending || 0) : null;
      var confirmed = stats ? Number(stats.confirmed || 0) : null;
      var rejected = stats ? Number(stats.rejected || 0) : null;
      var total = pending === null ? null : pending + confirmed + rejected;

      put("pending", pending === null ? "—" : pending);
      put("pendingNote", pending ? "需主管逐条确认" : "暂无待确认");
      put("approved", confirmed === null ? "—" : confirmed);
      put("approvedNote", "入库后与普通证据单元同等管理");
      put("rejected", rejected === null ? "—" : rejected);
      put("rejectedNote", "驳回理由已留痕，供复盘");
      put("total", total === null ? "—" : total);
      var channels = stats && stats.channels;
      if (channels) {
        var manual = Number(channels.manual || 0);
        put(
          "channelNote",
          "工单 " + Number(channels.ticket || 0)
            + " · 对话 " + Number(channels.favorite || 0)
            + (manual ? " · 手工 " + manual : "")
        );
      } else {
        put("channelNote", total === null ? "—" : "工单 / 收藏 / 手工 同一审核状态机");
      }
    }

    /* ---------------- 列表 ---------------- */
    function filteredItems() {
      var keyword = state.keyword;
      if (!keyword) return state.items;
      return state.items.filter(function (item) {
        var channelLabel = channelOf(item.channel).label;
        var haystack = [item.title, item.model, item.source, channelLabel].join(" ").toLowerCase();
        return haystack.indexOf(keyword) >= 0;
      });
    }

    function itemHtml(item) {
      var channel = channelOf(item.channel);
      var status = STATUS[item.status] || STATUS.pending;
      var who = item.submittedBy === "未识别"
        ? '<span class="idtag idtag--amber" title="未识别身份：内容仍可审核入库，只是作者不可归属">未识别</span>'
        : UI.esc(dash(item.submittedBy));
      var meta = [
        '<span class="ch"><i class="dot ' + (CH_DOT[item.channel] || "dot--gray") + '"></i>' + UI.esc(channel.label) + "</span>",
        item.model ? "<span>" + UI.esc(item.model) + "</span>" : "",
        item.batch ? "<span>" + UI.esc(item.batch) + "</span>" : "",
        '<span class="settle-item__who">' + who + " · " + UI.esc(item.submittedAtLabel) + "</span>",
      ].join("");

      return '<div class="settle-item ' + (item.id === state.selectedId ? "is-active" : "") + '" data-id="' + UI.esc(item.id) + '"'
        + ' title="' + UI.esc(item.source || item.title) + '">'
        + '<span class="t-ic ' + (item.channel === "favorite" ? "t-ic--chat" : item.channel === "ticket" ? "t-ic--ticket" : "t-ic--manual") + '">'
        + (CH_ICON[item.channel] || CH_ICON.manual) + "</span>"
        + "<div>"
        + '<div class="settle-item__title">' + UI.esc(item.title) + "</div>"
        + '<div class="settle-item__meta">' + meta + "</div>"
        + "</div>"
        + '<span class="tag ' + status.cls + ' settle-item__status">' + status.label + "</span>"
        + "</div>";
    }

    function renderList() {
      var rows = filteredItems();
      emptyEl.hidden = state.loading || !!state.loadError || rows.length > 0;

      if (state.loading) {
        noteEl.textContent = "加载中…";
        listEl.innerHTML = "";
        return;
      }
      if (state.loadError) {
        noteEl.textContent = "数据加载失败";
        listEl.innerHTML = '<div class="empty">数据加载失败：' + UI.esc(state.loadError) + "</div>";
        return;
      }

      noteEl.textContent = "共 " + state.total + " 条候选 · 当前筛选 " + rows.length + " 条"
        + (state.total > state.items.length ? "（本页已加载 " + state.items.length + " 条）" : "");
      if (!rows.length) {
        listEl.innerHTML = "";
        /* 空队列只留一句"为什么空"；候选从哪来、是否自动入库的完整说明放在页面底部提示语。
           空态写太满会把列表区域撑成一块大段文字，看起来像说明页而不是审核台。 */
        emptyEl.innerHTML = state.total === 0
          ? '<svg viewBox="0 0 24 24"><path d="m19 21-7-4-7 4V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2v16z"/></svg>'
            + "<b>还没有候选条目</b><br/>"
            + "等工程师收藏回答、或主管沉淀已完工单后，这里会出现待确认候选。"
          : '<svg viewBox="0 0 24 24"><path d="m19 21-7-4-7 4V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2v16z"/></svg>'
            + "没有匹配的候选条目，切换状态或清除筛选";
        return;
      }
      if (!rows.some(function (item) { return item.id === state.selectedId; })) {
        state.selectedId = rows[0].id;
      }
      listEl.innerHTML = rows.map(itemHtml).join("");
    }

    /* ---------------- 详情 ---------------- */
    function renderReviewLog(item) {
      var rows = item.reviewLog || [];
      if (!rows.length) {
        return '<div class="hint"><svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/>'
          + '<path d="M12 16v-4M12 8h.01"/></svg>尚无审核记录：这条候选还没被人处理过。</div>';
      }
      return '<div class="chain">' + rows.map(function (row) {
        var action = REVIEW_ACTION[row.action] || { label: row.action || "操作", bad: false };
        return '<div class="chain__step ' + (action.bad ? "is-bad" : "") + '">'
          + '<div class="chain__title">' + UI.esc(action.label) + " · " + UI.esc(row.by || "—")
          + ' <span style="font-weight:400;color:#8a97a3"> · ' + UI.esc(row.at || "") + "</span></div>"
          + '<div class="chain__body">' + UI.esc(row.note || "") + "</div>"
          + "</div>";
      }).join("") + "</div>";
    }

    function renderContext(item) {
      var rows = item.context || [];
      if (!rows.length) {
        return '<div class="hint"><svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/>'
          + '<path d="M12 16v-4M12 8h.01"/></svg>没有来源上下文：只按候选正文审核。</div>';
      }
      return '<dl class="kv">' + rows.map(function (row) {
        return "<dt>" + UI.esc(row.k) + "</dt><dd>" + UI.esc(row.v) + "</dd>";
      }).join("") + "</dl>";
    }

    function renderDetail() {
      var item = state.items.filter(function (row) { return row.id === state.selectedId; })[0];
      if (!item) {
        detailEl.innerHTML = state.loading
          ? '<div class="empty">正在从接口加载候选条目…</div>'
          : (state.loadError
            ? '<div class="empty">数据加载失败：' + UI.esc(state.loadError) + "</div>"
            : '<div class="empty">左侧选择一条候选条目</div>');
        return;
      }

      var channel = channelOf(item.channel);
      var status = STATUS[item.status] || STATUS.pending;
      var isPending = item.status === "pending";
      var readonlyTip = "随沉淀带入库；入库后可在「知识源管理」中修正";
      var metaInputs = [
        { label: "适用设备型号", value: item.model },
        { label: "适用批次", value: item.batch },
        { label: "手册版本", value: item.version },
      ].map(function (field) {
        return '<div><label>' + field.label + "</label>"
          + '<input value="' + UI.esc(dash(field.value)) + '" readonly title="' + readonlyTip + '" /></div>';
      }).join("");
      var locInput = isPending
        ? '<div><label>出处定位</label><input id="stLoc" value="' + UI.esc(item.loc || "")
          + '" placeholder="如：工单 ZX-2026-101 · 处置记录" /></div>'
        : '<div><label>出处定位</label><input value="' + UI.esc(dash(item.loc)) + '" readonly /></div>';

      detailEl.innerHTML = `
        <div class="detail__section">
          <div class="detail__label">
            <svg viewBox="0 0 24 24"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/></svg>
            候选证据原文（待确认的入库内容）
            <span style="margin-left:auto;display:flex;gap:6px">
              <span class="tag ${channel.cls}">${UI.esc(channel.label)}</span>
              <span class="tag ${status.cls}">${status.label}</span>
            </span>
          </div>
          <div class="evidence">${UI.esc(item.text)}</div>
          <div class="hint">
            <svg viewBox="0 0 24 24"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>
            入库后它就是一条普通证据单元，必须在「切片预览」中能查到出处；来源上下文一并保留，供审核追溯。
          </div>
        </div>

        <div class="detail__section">
          <div class="detail__label">
            <svg viewBox="0 0 24 24"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
            来源上下文（${UI.esc(item.source || "未记录")}）
          </div>
          ${renderContext(item)}
        </div>

        <div class="detail__section">
          <div class="detail__label">
            <svg viewBox="0 0 24 24"><path d="M16 18 22 12l-6-6M8 6l-6 6 6 6"/></svg>
            入库元数据（确认前可修正出处定位；批次填错会造成跨批次误召回）
          </div>
          <div class="meta-grid">${metaInputs}${locInput}</div>
          <div class="sub">型号 / 批次 / 版本随沉淀字段入库；本批次接口只在确认时允许修正正文与出处定位，其余元数据可在入库后到「知识源管理」继续修正。</div>
        </div>

        <div class="detail__section">
          <div class="detail__label">
            <svg viewBox="0 0 24 24"><path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/></svg>
            审核记录
          </div>
          ${renderReviewLog(item)}
        </div>

        <div class="detail__section">
          <div class="detail__ops">
            ${isPending ? `
              <span class="spacer"></span>
              <button class="btn btn--danger-ghost" id="btnReject">驳回</button>
              <button class="btn btn--primary" id="btnApprove">
                <svg viewBox="0 0 24 24"><path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/></svg>
                确认入库
              </button>` : `<span class="tag ${status.cls}" style="height:26px">${status.label}</span>
                 <span class="spacer"></span>
                 <span style="font-size:11.5px;color:#8a97a3">${item.status === "confirmed"
                   ? "已入库，可在「知识源管理」中继续修正元数据或禁用"
                   : "已驳回，不入库；如判断有误可由管理员重新提交候选"}</span>`}
          </div>
        </div>`;

      if (isPending) {
        document.getElementById("btnApprove").addEventListener("click", function () { approve(item); });
        document.getElementById("btnReject").addEventListener("click", function () { openReject(item); });
      }
    }

    /* ---------------- 状态流转 ---------------- */
    function approve(item) {
      var btn = document.getElementById("btnApprove");
      var locEl = document.getElementById("stLoc");
      var payload = { note: "确认入库：人工核对候选正文与来源上下文" };
      if (locEl) {
        var loc = locEl.value.trim();
        // 只在管理员真的改过出处定位时提交覆盖；清空也要提交（表示"确认时去掉定位"）
        if (loc !== (item.loc || "")) payload.loc = loc;
      }
      if (btn) btn.disabled = true;

      API.confirm(item.id, payload)
        .then(function (updated) {
          var vec = updated && updated.vectorization;
          if (vec && vec.status === "failed") {
            UI.toast("已入库《" + shortTitle(item.title) + "》，但补向量失败：当前仅关键词可检索");
          } else if (vec && vec.status === "skipped") {
            UI.toast("已入库《" + shortTitle(item.title) + "》，未配置向量服务，仅关键词可检索");
          } else {
            UI.toast("已入库并生成向量《" + shortTitle(item.title) + "》，Dense + BM25 均可命中");
          }
          if (window.AdminLayout) window.AdminLayout.refreshBadges();
          load(updated.id);
        })
        .catch(function (err) {
          if (err && err.status === 401) return;
          if (btn) btn.disabled = false;
          UI.toast((err && err.message) || "确认入库失败，请稍后再试");
        });
    }

    function openReject(item) {
      state.pendingReject = item;
      rejectNoteEl.value = "";
      resetRejectHint();
      rejectModal.hidden = false;
    }

    function resetRejectHint() {
      var hint = rejectModal.querySelector(".field .sub");
      if (!hint) return;
      hint.textContent = "驳回后条目不入库，理由写入审核记录，便于复盘与新人培训。";
      hint.style.color = "";
    }

    confirmRejectBtn.addEventListener("click", function () {
      var note = rejectNoteEl.value.trim();
      var hint = rejectModal.querySelector(".field .sub");
      if (!note) {
        if (hint) {
          hint.textContent = "驳回必须填写理由（这是复盘与培训的依据）";
          hint.style.color = "#c0392b";
        }
        return;
      }
      var item = state.pendingReject;
      if (!item) return;

      confirmRejectBtn.disabled = true;
      API.reject(item.id, { note: note })
        .then(function (updated) {
          state.pendingReject = null;
          rejectModal.hidden = true;
          UI.toast("已驳回，理由写入审核记录");
          if (window.AdminLayout) window.AdminLayout.refreshBadges();
          load(updated.id);
        })
        .catch(function (err) {
          if (err && err.status === 401) return;
          UI.toast((err && err.message) || "驳回失败，请稍后再试");
        })
        .then(function () {
          confirmRejectBtn.disabled = false;
        });
    });

    /* ---------------- 交互 ---------------- */
    function render() {
      renderStats();
      renderList();
      renderDetail();
    }

    listEl.addEventListener("click", function (e) {
      var item = e.target.closest(".settle-item");
      if (!item) return;
      state.selectedId = item.dataset.id;
      render();
    });

    document.getElementById("statusTabs").addEventListener("click", function (e) {
      var btn = e.target.closest("button[data-st]");
      if (!btn) return;
      state.statusFilter = btn.dataset.st;
      Array.prototype.forEach.call(
        document.querySelectorAll("#statusTabs button"),
        function (node) { node.classList.toggle("on", node === btn); }
      );
      state.selectedId = null;
      load();
    });

    document.getElementById("searchInput").addEventListener("input", function (e) {
      state.keyword = e.target.value.trim().toLowerCase();
      state.selectedId = null;
      render();
    });

    load();
  };
})();
