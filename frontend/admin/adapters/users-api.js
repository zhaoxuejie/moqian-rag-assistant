/* ============================================================
 * users-api.js — 「用户管理 / 我的账号」两页的接口适配层
 *
 * 分层约定：
 *   - 只做 HTTP 与数据整形，**不碰 DOM**、不渲染、不发演示数据；
 *   - 页面 controller 只调用 here，不直接 fetch / XHR；
 *   - HTTP 通道复用 real-api.js 暴露的 window.AdminAPI（登录态、401 跳登录、错误体都一致）。
 *
 * 覆盖接口（contracts/openapi.yaml）：
 *   GET    /admin/users                         → AdminUserPage
 *   POST   /admin/users                         → AdminUser（201，重复工号 409）
 *   PATCH  /admin/users/{userId}                → AdminUser
 *   DELETE /admin/users/{userId}                → 204（软删：status=disabled）
 *   POST   /admin/users/{userId}/reset-password → {ok, temporaryPassword}
 *   GET    /admin/identity/pending              → {items: PendingAccount[]}
 *   POST   /admin/identity/pending/{account}/activate → AdminUser（201）
 *   PUT    /admin/me/password                   → {ok}
 *   GET    /admin/me/messages                   → QaMessagePage
 *   GET    /admin/me/saved                      → SavedItemPage
 *
 * 首屏沿用 real-api.js 的同步取数方式（页面脚本执行前数据就绪），交互走异步。
 * 已知限制（与 real-api.js 同口径）：列表一次取 pageSize=100，超过 100 个账号时
 * 页面只展示前 100 个，页脚会明确写出"已加载 X / 共 N"。
 * ============================================================ */
(function () {
  "use strict";

  var AdminAPI = window.AdminAPI;

  if (!AdminAPI) {
    /* real-api.js 未加载：保持 window.UsersApi 存在，页面显示"接口层未就绪"而不是报错 */
    window.UsersApi = {
      available: false,
      source: "users-api-missing",
      boot: function () { return { available: false, error: "接口适配层未就绪（缺少 real-api.js）" }; },
      data: {},
    };
    return;
  }

  var LIST_PAGE_SIZE = 100;

  var cache = {
    available: true,
    users: [],
    pagination: null,
    pending: [],
    messages: [],
    messagesPagination: null,
    saved: [],
    savedPagination: null,
    me: null,
    forbidden: false,
    unauthorized: false,
    error: null,
  };

  /* ---------------- 契约 → 页面形状（字段改名 / 空值兜底，不改数值口径） ---------------- */
  function mapUser(raw) {
    var user = raw || {};
    return {
      id: String(user.id == null ? "" : user.id),
      account: user.account || "",
      name: user.name || "",
      role: user.role === "admin" ? "admin" : "user",
      status: user.status === "disabled" ? "disabled" : "active",
      credential: user.credential === "domain" ? "domain" : "local",
      team: user.team || null,
      lastActive: user.lastActive || null,
      isSentinel: !!user.isSentinel,
    };
  }

  function mapPending(raw) {
    var item = raw || {};
    var count = Number(item.count);
    return {
      account: item.account || "",
      count: isFinite(count) ? count : 0,
      lastAt: item.lastAt || null,
    };
  }

  function mapMessage(raw) {
    var item = raw || {};
    return {
      id: String(item.id == null ? "" : item.id),
      time: item.time || null,
      sessionId: item.sessionId || null,
      user: item.user || null,
      ticket: item.ticket || null,
      identity: item.identity || { source: "anonymous" },
      question: item.question || "",
      structured: item.structured || null,
      hits: item.hits || [],
      citations: Number(item.citations) || 0,
      outcome: item.outcome || "error",
      resolved: item.resolved || null,
      resolvedInSec: typeof item.resolvedInSec === "number" ? item.resolvedInSec : null,
      handoff: !!item.handoff,
      channel: item.channel || "widget",
    };
  }

  function mapSaved(raw) {
    var item = raw || {};
    return {
      id: String(item.id == null ? "" : item.id),
      question: item.question || "",
      messageId: String(item.messageId == null ? "" : item.messageId),
      savedAt: item.savedAt || null,
      citations: item.citations || [],
    };
  }

  function noteError(res) {
    if (!res || res.ok) return;
    if (res.unauthorized) {
      cache.unauthorized = true;
      return;
    }
    if (res.status === 403) {
      cache.forbidden = true;
      return;
    }
    cache.error = (res.error && res.error.message) || "数据加载失败";
  }

  function currentUser() {
    try {
      return window.AdminAuth && window.AdminAuth.user ? window.AdminAuth.user() : null;
    } catch (e) {
      return null;
    }
  }

  /* ---------------- 首屏（同步）：users 页拉账号 + 待开通；account 页拉我的问答/收藏 ---------------- */
  function boot(page) {
    cache.forbidden = false;
    cache.unauthorized = false;
    cache.error = null;
    cache.me = currentUser();

    if (page === "users") {
      if (cache.me && cache.me.role !== "admin") {
        /* 普通用户没有治理域权限：不打接口，直接让 controller 跳回个人域 */
        cache.forbidden = true;
        return cache;
      }
      var users = AdminAPI.requestSync("GET", "/users", { page: 1, pageSize: LIST_PAGE_SIZE });
      if (users.ok && users.data) {
        cache.users = (users.data.items || []).map(mapUser);
        cache.pagination = users.data.pagination || null;
      } else {
        noteError(users);
      }
      if (!cache.forbidden) {
        var pending = AdminAPI.requestSync("GET", "/identity/pending");
        if (pending.ok && pending.data) {
          cache.pending = (pending.data.items || []).map(mapPending);
        } else {
          noteError(pending);
        }
      }
      return cache;
    }

    if (page === "account") return cache;

    if (page === "my-qa") {
      reloadMessagesSync({ page: 1, pageSize: 10 });
      return cache;
    }

    if (page === "my-save") {
      reloadSavedSync({ page: 1, pageSize: 10 });
      return cache;
    }

    return cache;
  }

  function reloadMessagesSync(query) {
    var res = AdminAPI.requestSync("GET", "/me/messages", query);
    if (res.ok && res.data) {
      cache.messages = (res.data.items || []).map(mapMessage);
      cache.messagesPagination = res.data.pagination || null;
    } else {
      noteError(res);
    }
    return cache.messages;
  }

  function reloadSavedSync(query) {
    var res = AdminAPI.requestSync("GET", "/me/saved", query);
    if (res.ok && res.data) {
      cache.saved = (res.data.items || []).map(mapSaved);
      cache.savedPagination = res.data.pagination || null;
    } else {
      noteError(res);
    }
    return cache.saved;
  }

  /* ---------------- 异步操作 ---------------- */
  function listUsers(query) {
    return AdminAPI.request("GET", "/users", { query: query }).then(function (data) {
      cache.users = (data.items || []).map(mapUser);
      cache.pagination = data.pagination || null;
      return cache.users;
    });
  }

  function loadPending() {
    return AdminAPI.request("GET", "/identity/pending").then(function (data) {
      cache.pending = (data.items || []).map(mapPending);
      return cache.pending;
    });
  }

  function createUser(body) {
    return AdminAPI.request("POST", "/users", { json: body });
  }

  function patchUser(id, patch) {
    return AdminAPI.request("PATCH", "/users/" + encodeURIComponent(id), { json: patch });
  }

  function removeUser(id) {
    return AdminAPI.request("DELETE", "/users/" + encodeURIComponent(id));
  }

  function resetPassword(id) {
    return AdminAPI.request("POST", "/users/" + encodeURIComponent(id) + "/reset-password");
  }

  function activatePending(account, body) {
    /* 契约的开通请求不带口令：local 账号开通后立刻重置一次，把一次性临时口令交给管理员
       （后端也不会把随机初始散列的明文返回，避免"谁都不知道密码"的账号悬空）。 */
    return AdminAPI.request(
      "POST",
      "/identity/pending/" + encodeURIComponent(account) + "/activate",
      { json: body }
    ).then(function (user) {
      if (body && body.credential === "local") {
        return resetPassword(user.id)
          .then(function (result) {
            return { user: user, temporaryPassword: (result && result.temporaryPassword) || null };
          })
          .catch(function () {
            return { user: user, temporaryPassword: null, resetFailed: true };
          });
      }
      return { user: user, temporaryPassword: null };
    });
  }

  function changePassword(oldPassword, newPassword) {
    return AdminAPI.request("PUT", "/me/password", {
      json: { oldPassword: oldPassword, newPassword: newPassword },
    });
  }

  function reloadMessages(query) {
    return AdminAPI.request("GET", "/me/messages", { query: query }).then(function (data) {
      cache.messages = (data.items || []).map(mapMessage);
      cache.messagesPagination = data.pagination || null;
      return cache.messages;
    });
  }

  function reloadSaved(query) {
    return AdminAPI.request("GET", "/me/saved", { query: query }).then(function (data) {
      cache.saved = (data.items || []).map(mapSaved);
      cache.savedPagination = data.pagination || null;
      return cache.saved;
    });
  }

  window.UsersApi = {
    source: "users-api",
    available: true,
    data: cache,
    boot: boot,
    me: currentUser,
    mapUser: mapUser,
    listUsers: listUsers,
    loadPending: loadPending,
    createUser: createUser,
    patchUser: patchUser,
    removeUser: removeUser,
    resetPassword: resetPassword,
    activatePending: activatePending,
    changePassword: changePassword,
    reloadMessages: reloadMessages,
    reloadSaved: reloadSaved,
  };
})();
