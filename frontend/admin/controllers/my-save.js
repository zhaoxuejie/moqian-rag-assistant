/* ============================================================
 * my-save.js — 我的收藏（个人域只读视图，从 account.js 拆出）
 *
 * 数据源：window.UsersApi.boot("my-save")（GET /admin/me/saved）
 * 只做渲染与分页交互，不发请求、不造数据。
 * ============================================================ */
(function () {
  "use strict";
  window.AdminPages = window.AdminPages || {};

  window.AdminPages["my-save"] = function () {
    var UI = window.UI;
    var api = window.UsersApi;
    if (!UI || !api) return;

    var boot = api.boot("my-save");
    var esc = UI.esc;
    var toast = UI.toast;
    var $ = function (id) { return document.getElementById(id); };
    var PAGE_SIZE = 10;
    var saved = boot.saved || [];
    var pagination = boot.savedPagination || {};

    function renderFav() {
      var box = $("favBox");
      if (!saved.length) {
        box.innerHTML = '<div class="empty" style="border:0;padding:18px 0">'
          + "还没有收藏记录。在助手面板里点「收藏」，这里就能看见你保存过的回答</div>";
        return;
      }
      box.innerHTML = saved.map(function (item) {
        var citations = item.citations || [];
        var evidence = citations.slice(0, 3).map(function (citation) {
          return '<span class="mono">' + esc(citation.evidenceId || "—") + "</span>";
        }).join("、");
        return '<div class="fav-item">'
          + '<div class="fav-item__head"><b>' + esc(item.question || "（未记录原始问题）") + "</b>"
          + '<span class="tag tag--green">已收藏</span></div>'
          + '<div class="sub">收藏于 ' + esc(item.savedAt || "—")
          + " · 引用 " + citations.length + " 条"
          + (item.messageId ? " · 消息 " + esc(item.messageId) : "")
          + "</div>"
          + '<div class="fav-item__note">'
          + (evidence ? "依据：" + evidence + (citations.length > 3 ? " 等" : "") : "该回答没有引用")
          + "</div>"
          + "</div>";
      }).join("");
    }

    function renderFavPager() {
      UI.fixedPager(
        $("favPager"),
        Number(pagination.total || saved.length),
        Number(pagination.page || 1),
        PAGE_SIZE,
        loadFav
      );
    }

    function loadFav(page) {
      return api.reloadSaved({ page: page, pageSize: PAGE_SIZE })
        .then(function (rows) {
          saved = rows || [];
          pagination = api.data.savedPagination || {};
          renderFav();
          renderFavPager();
        })
        .catch(function (ex) {
          if (ex && ex.status === 401) return;
          toast((ex && ex.message) || "收藏记录加载失败");
        });
    }

    renderFav();
    renderFavPager();
    if (boot.error) toast("数据加载失败：" + boot.error);
  };
})();
