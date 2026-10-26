/* ============================================================
 * gaps.js — 知识缺口页控制器（DOM / 交互）
 *
 * 数据来源：adapters/logs-api.js（契约 → 页面结构），本文件不直接 fetch。
 * 口径（见 contracts/openapi.yaml）：
 *   - 缺口来自「证据不足拒答」的自动归集，按型号 + 现象汇总；
 *   - 「标记覆盖」必须指明是哪份知识补上的，可「撤销覆盖」回到未覆盖；
 *   - 接口只有 status 筛选，关键词搜索只过滤**已加载的**清单（本页数据），不伪装成全量搜索。
 * ============================================================ */
(function () {
  "use strict";
  window.AdminPages = window.AdminPages || {};

  function initGaps() {
    const UI = window.UI || {};
    const LogsAPI = window.LogsAPI;
    const tbody = document.getElementById("gapRows");
    const emptyEl = document.getElementById("gapEmpty");
    const noteEl = document.getElementById("gapNote");
    const pagerEl = document.getElementById("gapPager");
    const coverModal = document.getElementById("coverModal");
    if (!tbody || !emptyEl || !LogsAPI) return;

    const esc = UI.esc || function (value) { return String(value == null ? "" : value); };
    const STATUS_LABEL = LogsAPI.GAP_STATUS_LABEL;
    const GAPS_PAGE_SIZE_KEY = "moqian_gaps_page_size";

    const state = {
      page: 1,
      pageSize: UI.readPageSize ? UI.readPageSize(GAPS_PAGE_SIZE_KEY, 10) : 10,
      status: "all",
      keyword: "",
    };
    let items = [];
    let total = 0;
    let stats = null;
    let documents = [];
    let pendingCover = null;
    let loadSeq = 0;

    const put = function (key, value) {
      const el = document.querySelector('[data-stat="' + key + '"]');
      if (el) el.textContent = value;
    };

    function docTitle(id) {
      if (!id) return "—";
      const found = documents.filter(function (doc) { return doc.id === id; })[0];
      return found ? found.title + " · " + found.version : id;
    }

    /* ---------------- 统计卡 ---------------- */

    function renderStats() {
      const openItems = items.filter(function (item) { return item.status === "open"; });
      const coveredItems = items.filter(function (item) { return item.status === "covered"; });
      const open = stats && typeof stats.uncovered === "number" ? stats.uncovered : openItems.length;
      const covered = stats && typeof stats.covered === "number"
        ? stats.covered
        : coveredItems.length;
      const hits = items.reduce(function (sum, item) { return sum + item.count; }, 0);

      // 最集中型号：按未覆盖缺口的累计次数排序（基于已加载清单）
      const byModel = {};
      openItems.forEach(function (item) {
        if (!item.model) return;
        byModel[item.model] = (byModel[item.model] || 0) + item.count;
      });
      const models = Object.keys(byModel).sort(function (a, b) {
        return byModel[b] - byModel[a];
      });
      const topModel = models.length ? models[0] : null;

      put("open", open);
      put("openNote", open ? "集中在 " + (topModel || "—") + " 型号" : "全部已覆盖");
      put("covered", covered);
      put("coveredNote", "已由新知识补上");
      put("hits", hits);
      put("hitsNote", "已加载清单的累计提问次数");
      put("topModel", topModel || "—");
      put("topModelNote", topModel ? "未覆盖缺口 " + byModel[topModel] + " 次" : "—");
      if (noteEl) {
        noteEl.textContent = "共 " + total + " 条缺口 · 未覆盖 " + open + " 条"
          + (items.length < total ? "（已加载 " + items.length + " 条，可继续用状态筛选）" : "");
      }
    }

    /* ---------------- 列表 ---------------- */

    function filtered() {
      const keyword = state.keyword.trim().toLowerCase();
      return items.filter(function (item) {
        if (!keyword) return true;
        return ((item.model || "") + item.symptom).toLowerCase().indexOf(keyword) >= 0;
      });
    }

    function showListError(message) {
      items = [];
      total = 0;
      stats = null;
      tbody.innerHTML = "";
      emptyEl.hidden = false;
      emptyEl.innerHTML = '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/>'
        + '<path d="M12 8v4M12 16h.01"/></svg>知识缺口加载失败：' + esc(message);
      if (pagerEl) pagerEl.innerHTML = "";
      renderStats();
    }

    function renderTable() {
      const list = filtered();
      const pages = Math.max(1, Math.ceil(list.length / state.pageSize));
      if (state.page > pages) state.page = pages;
      const pageRows = list.slice((state.page - 1) * state.pageSize, state.page * state.pageSize);

      emptyEl.hidden = list.length > 0;
      if (!list.length) {
        tbody.innerHTML = "";
        if (!state.keyword) {
          emptyEl.textContent = "当前无知识缺口——说明现有资料覆盖了工程师提出的问题";
        }
        return;
      }

      tbody.innerHTML = pageRows.map(function (item) {
        const coveredSub = item.status === "covered"
          ? '<div class="sub">已由《' + esc(docTitle(item.coveredBy).slice(0, 26)) + "…》覆盖</div>"
          : '<div class="sub">工程师问过但知识库答不出，建议补资料</div>';
        const statusTag = item.status === "open"
          ? '<span class="tag tag--amber"><i class="dot dot--amber"></i>未覆盖</span>'
          : item.status === "covered"
            ? '<span class="tag tag--green"><i class="dot dot--green"></i>已覆盖</span>'
            : '<span class="tag tag--gray"><i class="dot dot--gray"></i>已忽略</span>';
        const action = item.status === "open"
          ? '<button class="iconbtn iconbtn--text iconbtn--ok" data-cover="' + esc(item.id)
            + '" title="标记覆盖：指明是哪份知识补上的">标记覆盖</button>'
          : '<button class="iconbtn iconbtn--text" data-reopen="' + esc(item.id)
            + '" title="撤销覆盖，让缺口回到未覆盖">撤销覆盖</button>';
        return '<tr data-id="' + esc(item.id) + '">'
          + '<td><span class="tag tag--gray">' + esc(item.model || "未标注") + "</span></td>"
          + "<td><div style=\"font-weight:600\">" + esc(item.symptom) + "</div>"
          + coveredSub + "</td>"
          + '<td class="num">' + item.count + "</td>"
          + '<td style="color:#52606d">' + esc(item.firstAt) + "</td>"
          + '<td style="color:#52606d">' + esc(item.lastAt) + "</td>"
          + "<td>" + statusTag + "</td>"
          + '<td class="cell-actions"><div class="row-actions">' + action + "</div></td>"
          + "</tr>";
      }).join("");

      renderPager(list.length);
    }

    function renderPager(visibleTotal) {
      if (!pagerEl || !UI.pager) return;
      UI.pager(pagerEl, visibleTotal, state.page, state.pageSize, function (page, pageSize) {
        state.page = page;
        state.pageSize = pageSize;
        renderTable();
      }, GAPS_PAGE_SIZE_KEY);
    }

    function render() {
      renderStats();
      renderTable();
    }

    function load() {
      const seq = ++loadSeq;
      tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;color:#8a97a3">'
        + "加载中…</td></tr>";
      emptyEl.hidden = true;
      const params = { page: 1, pageSize: 100, withStats: true };
      if (state.status !== "all") params.status = state.status;

      LogsAPI.listGaps(params).then(function (data) {
        if (seq !== loadSeq) return;
        items = data.items;
        total = data.pagination.total;
        stats = data.stats;
        state.page = 1;
        render();
      }).catch(function (ex) {
        if (seq !== loadSeq) return;
        if (ex && ex.status === 401) return;
        showListError((ex && ex.message) || "请稍后重试");
        if (window.toast) toast("知识缺口加载失败：" + ((ex && ex.message) || "请稍后重试"));
      });
    }

    /* ---------------- 标记覆盖 / 撤销覆盖 ---------------- */

    function loadDocuments() {
      return LogsAPI.listDocuments().then(function (list) {
        documents = list;
      }).catch(function (ex) {
        if (ex && ex.status === 401) return;
        documents = [];
      });
    }

    function openCover(gap) {
      if (!coverModal) return;
      pendingCover = gap;
      const textEl = document.getElementById("coverText");
      if (textEl) {
        textEl.textContent = "「" + (gap.model || "未标注型号") + " · " + gap.symptom
          + "」累计被问了 " + gap.count + " 次。请指明是哪份知识补上了这个缺口：";
      }
      const select = document.getElementById("coverDoc");
      if (select) {
        if (!documents.length) {
          select.innerHTML = '<option value="">（暂无可选知识源）</option>';
        } else {
          select.innerHTML = documents.map(function (doc) {
            return '<option value="' + esc(doc.id) + '">' + esc(doc.title)
              + " · " + esc(doc.version) + "</option>";
          }).join("");
        }
      }
      coverModal.hidden = false;
    }

    function closeCover() {
      pendingCover = null;
      if (coverModal) coverModal.hidden = true;
    }

    function markCovered() {
      if (!pendingCover) return;
      const select = document.getElementById("coverDoc");
      const docId = select ? select.value : "";
      if (!docId) {
        if (window.toast) toast("请先选择补上这个缺口的资料");
        return;
      }
      const button = document.getElementById("confirmCover");
      if (button) {
        button.disabled = true;
        button.textContent = "保存中…";
      }
      LogsAPI.patchGap(pendingCover.id, { status: "covered", coveredBy: docId })
        .then(function () {
          closeCover();
          if (window.AdminLayout && window.AdminLayout.refreshBadges) {
            window.AdminLayout.refreshBadges();
          }
          if (window.toast) {
            toast("缺口已标记覆盖：由《" + docTitle(docId).slice(0, 16) + "…》补上");
          }
          load();
        })
        .catch(function (ex) {
          if (ex && ex.status === 401) return;
          if (window.toast) toast((ex && ex.message) || "标记覆盖失败，请稍后重试");
          if (button) {
            button.disabled = false;
            button.textContent = "确认已覆盖";
          }
        });
    }

    function reopenGap(gap) {
      if (!gap) return;
      LogsAPI.patchGap(gap.id, { status: "open" })
        .then(function () {
          if (window.AdminLayout && window.AdminLayout.refreshBadges) {
            window.AdminLayout.refreshBadges();
          }
          if (window.toast) toast("已撤销覆盖标记，该缺口回到未覆盖列表");
          load();
        })
        .catch(function (ex) {
          if (ex && ex.status === 401) return;
          if (window.toast) toast((ex && ex.message) || "撤销覆盖失败，请稍后重试");
        });
    }

    /* ---------------- 交互 ---------------- */

    tbody.addEventListener("click", function (event) {
      const coverBtn = event.target.closest("[data-cover]");
      if (coverBtn) {
        const gap = items.filter(function (item) {
          return item.id === coverBtn.dataset.cover;
        })[0];
        if (gap) openCover(gap);
        return;
      }
      const reopenBtn = event.target.closest("[data-reopen]");
      if (reopenBtn) {
        const gap = items.filter(function (item) {
          return item.id === reopenBtn.dataset.reopen;
        })[0];
        reopenGap(gap);
      }
    });

    const tabs = document.getElementById("gapTabs");
    if (tabs) {
      tabs.addEventListener("click", function (event) {
        const btn = event.target.closest("button[data-gs]");
        if (!btn) return;
        state.status = btn.dataset.gs;
        tabs.querySelectorAll("button").forEach(function (item) {
          item.classList.toggle("on", item === btn);
        });
        load();
      });
    }

    const searchInput = document.getElementById("searchInput");
    if (searchInput) {
      searchInput.addEventListener("input", function (event) {
        state.keyword = event.target.value.trim();
        state.page = 1;
        renderTable();
      });
    }

    const confirmButton = document.getElementById("confirmCover");
    if (confirmButton) confirmButton.addEventListener("click", markCovered);

    // 弹窗关闭由 app.js 的 initModals 统一委托；这里同步清掉待保存状态
    if (coverModal) {
      coverModal.addEventListener("click", function (event) {
        if (event.target === coverModal || event.target.closest("[data-close]")) closeCover();
      });
    }

    loadDocuments();
    load();
  }

  window.AdminPages.gaps = initGaps;
})();
