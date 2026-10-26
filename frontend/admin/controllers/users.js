/* ============================================================
 * users.js — 用户管理（PRD FR-P1 / admin spec §4.11）
 *
 * 只有两级角色：admin（管理员/主管/实施方）与 user（普通用户/工程师）。
 * 刻意不做细粒度 RBAC，但"谁能做什么"要看得见（页面底部权限矩阵），
 * 而且账号要能管：新增、改角色、禁用/启用、重置密码、软删除、开通待开通工号。
 *
 * 数据源：window.UsersApi（adapters/users-api.js，真实接口）；
 * 本文件只做渲染与交互，不发请求、不造数据。
 * ============================================================ */
(function () {
  "use strict";
  window.AdminPages = window.AdminPages || {};

  window.AdminPages.users = function () {
    var UI = window.UI;
    var api = window.UsersApi;
    if (!UI || !api) return;

    var boot = api.boot("users");
    if (boot.forbidden) {
      /* 普通用户没有治理域权限：与原型一致，跳回个人域并说明原因 */
      location.replace("account.html?denied=users");
      return;
    }

    var state = {
      roleFilter: "all",
      kw: "",
      users: boot.users || [],
      pending: boot.pending || [],
      pagination: boot.pagination || null,
      error: boot.error || null,
    };

    var ROLE = {
      admin: { label: "管理员", cls: "tag--blue" },
      user: { label: "普通用户", cls: "tag--gray" },
    };
    var CRED = {
      domain: { label: "企业域账号", cls: "idtag--blue", resettable: false },
      local: { label: "平台密码", cls: "", resettable: true },
    };

    var $ = function (id) { return document.getElementById(id); };
    var esc = UI.esc;
    var toast = UI.toast;

    function put(key, value) {
      var el = document.querySelector('[data-stat="' + key + '"]');
      if (el) el.textContent = value;
    }

    function isRecent(lastActive) {
      var time = Date.parse(String(lastActive || "").replace(/-/g, "/"));
      return !isNaN(time) && Date.now() - time <= 7 * 24 * 3600 * 1000;
    }
    function adminCount() {
      return state.users.filter(function (u) { return u.role === "admin" && u.status === "active"; }).length;
    }
    function findUser(id) {
      return state.users.filter(function (u) { return String(u.id) === String(id); })[0] || null;
    }
    function currentUser() {
      try { return window.AdminAuth && window.AdminAuth.user ? window.AdminAuth.user() : null; }
      catch (e) { return null; }
    }
    function errorToast(ex, fallback) {
      if (!ex || ex.status === 401) return;
      toast((ex && ex.message) || fallback || "操作失败，请稍后再试");
    }

    /* ---------------- 统计（数字同源于账号列表，不写死） ---------------- */
    function renderStats() {
      var admins = state.users.filter(function (u) { return u.role === "admin" && u.status === "active"; });
      var members = state.users.filter(function (u) { return u.role === "user" && u.status === "active"; });
      var disabled = state.users.filter(function (u) { return u.status === "disabled"; });
      var active = state.users.filter(function (u) { return isRecent(u.lastActive); });
      put("admins", admins.length);
      put("adminsNote", admins.length < 2 ? "建议至少保留 2 个管理员" : "可管理知识库、评测与配置");
      put("members", members.length);
      put("membersNote", "普通用户 = 在册工程师；登录后台只看得到个人域");
      put("disabled", disabled.length);
      put("disabledNote", disabled.length ? "禁用后不可登录，记录保留" : "当前没有禁用账号");
      put("active", active.length);
      put("activeNote", "近 7 天内有会话记录");

      var total = state.pagination ? state.pagination.total : state.users.length;
      var note = "共 " + total + " 个账号 · 启用 " + (state.users.length - disabled.length) + " 个";
      if (state.pagination && total > state.users.length) {
        note += "（列表一次加载前 " + state.users.length + " 个）";
      }
      $("userNote").textContent = note;
    }

    /* ---------------- 待开通工号（PRD §2.3） ----------------
     * 宿主传了工号、但平台没有对应账号：说明人已经在用、账号没跟开。
     * 这是推广进度线索，不是错误——所以单独成卡，而不是混在账号列表里。 */
    function renderClaims() {
      var list = state.pending || [];
      var box = $("claimBox");
      var note = $("claimNote");
      note.textContent = list.length
        ? "共 " + list.length + " 个工号在宿主侧出现过、但平台未开通"
        : "当前没有待开通工号";
      if (!list.length) {
        box.innerHTML = '<div class="empty" style="border:0;padding:18px 0">'
          + "当前没有待开通工号——已声明工号都已有账号</div>";
        return;
      }
      box.innerHTML = list.map(function (item, index) {
        var countText = item.count > 0 ? "出现 <b>" + item.count + "</b> 次" : "未记录会话次数";
        return '<div class="claim-row">'
          + '<span class="claim-row__acc">' + esc(item.account) + "</span>"
          + "<span>" + countText + "</span>"
          + '<span style="color:#8a97a3">最近 ' + esc(item.lastAt || "—") + "</span>"
          + '<span class="spacer"></span>'
          + '<button class="iconbtn iconbtn--text iconbtn--ok" data-claim="' + index + '" type="button">开通账号</button>'
          + "</div>";
      }).join("");
    }

    /* ---------------- 账号列表 ---------------- */
    function filteredUsers() {
      return state.users.filter(function (u) {
        if (state.roleFilter === "admin" || state.roleFilter === "user") {
          // 已禁用账号只出现在「已禁用」里，不再混进管理员/普通用户两个角色页签
          if (u.status !== "active" || u.role !== state.roleFilter) return false;
        }
        if (state.roleFilter === "disabled" && u.status !== "disabled") return false;
        if (!state.kw) return true;
        var haystack = (u.name + u.account + (u.team || "")).toLowerCase();
        return haystack.indexOf(state.kw) >= 0;
      });
    }

    function roleSelect(user) {
      var me = currentUser();
      var isSelf = me && String(me.id) === String(user.id);
      return '<select class="role-pick" data-role="' + esc(user.id) + '"'
        + (isSelf ? ' disabled title="不能修改自己的角色，请让另一位管理员操作"' : "")
        + ' style="border:1px solid var(--line);border-radius:7px;padding:4px 8px;'
        + 'background:#fff;font-size:12px">'
        + '<option value="user"' + (user.role === "user" ? " selected" : "") + ">普通用户</option>"
        + '<option value="admin"' + (user.role === "admin" ? " selected" : "") + ">管理员</option>"
        + "</select>";
    }

    function actionButtons(user) {
      var cred = CRED[user.credential] || CRED.local;
      var reset = cred.resettable
        ? '<button class="iconbtn iconbtn--text" data-pwd="' + esc(user.id)
          + '" title="生成一次性临时密码" type="button">重置密码</button>'
        : '<button class="iconbtn iconbtn--text" disabled type="button"'
          + ' title="密码由企业域账号管理，本平台不保存密码">重置密码</button>';
      var toggle = user.status === "active"
        ? '<button class="iconbtn iconbtn--text iconbtn--danger" data-off="' + esc(user.id)
          + '" title="禁用后不可登录" type="button">禁用</button>'
        : '<button class="iconbtn iconbtn--text iconbtn--ok" data-on="' + esc(user.id)
          + '" title="恢复登录" type="button">启用</button>';
      var remove = '<button class="iconbtn iconbtn--danger" data-del="' + esc(user.id)
        + '" title="删除账号（软删除，不可恢复登录）" type="button">'
        + '<svg viewBox="0 0 24 24"><path d="M3 6h18"/><path d="M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2"/>'
        + '<path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/></svg></button>';
      return reset + toggle + remove;
    }

    function renderTable() {
      var list = filteredUsers();
      $("userEmpty").hidden = list.length > 0;
      $("userRows").innerHTML = list.map(function (u) {
        var cred = CRED[u.credential] || CRED.local;
        return "<tr data-id=\"" + esc(u.id) + "\">"
          + '<td style="font-weight:600">' + esc(u.name) + "</td>"
          + '<td style="color:#52606d;font-family:ui-monospace,Consolas,monospace;font-size:12px">'
          + esc(u.account) + "</td>"
          + "<td>" + roleSelect(u) + "</td>"
          + "<td>" + (u.status === "active"
            ? '<span class="tag tag--green"><i class="dot dot--green"></i>启用中</span>'
            : '<span class="tag tag--red"><i class="dot dot--red"></i>已禁用</span>') + "</td>"
          + '<td><span class="idtag ' + cred.cls + '">' + cred.label + "</span></td>"
          + '<td style="color:#52606d">' + esc(u.team || "—") + "</td>"
          + '<td style="color:#52606d">' + esc(u.lastActive || "—") + "</td>"
          + '<td class="cell-actions"><div class="row-actions">' + actionButtons(u) + "</div></td>"
          + "</tr>";
      }).join("");
    }

    function render() {
      renderStats();
      renderClaims();
      renderTable();
    }

    /* ---------------- 刷新（写操作后回到真实数据，不做本地假更新） ---------------- */
    function refresh() {
      return Promise.all([
        api.listUsers({ page: 1, pageSize: 100 }),
        api.loadPending(),
      ]).then(function (results) {
        state.users = results[0] || [];
        state.pending = results[1] || [];
        state.pagination = api.data.pagination;
        render();
      }).catch(function (ex) {
        errorToast(ex, "刷新账号列表失败");
      });
    }

    /* ---------------- 改角色 ---------------- */
    $("userRows").addEventListener("change", function (event) {
      var select = event.target.closest("[data-role]");
      if (!select) return;
      var user = findUser(select.getAttribute("data-role"));
      if (!user || user.role === select.value) return;
      var me = currentUser();
      if (me && String(me.id) === String(user.id) && select.value !== "admin") {
        select.value = user.role;
        toast("不能修改自己的角色，请让另一位管理员操作");
        return;
      }
      if (user.role === "admin" && select.value === "user" && adminCount() <= 1) {
        select.value = user.role;
        toast("不能把最后一个管理员降为普通用户");
        return;
      }
      api.patchUser(user.id, { role: select.value })
        .then(function () {
          toast("已把「" + user.name + "」的角色改为" + ROLE[select.value].label);
          if (window.AdminLayout) window.AdminLayout.refreshUser();
          return refresh();
        })
        .catch(function (ex) {
          errorToast(ex, "改角色失败");
          render();
        });
    });

    /* ---------------- 禁用 / 启用 / 重置密码 / 删除 ---------------- */
    $("userRows").addEventListener("click", function (event) {
      var off = event.target.closest("[data-off]");
      var on = event.target.closest("[data-on]");
      var pwd = event.target.closest("[data-pwd]");
      var del = event.target.closest("[data-del]");

      if (off) {
        var offUser = findUser(off.getAttribute("data-off"));
        if (!offUser) return;
        if (offUser.role === "admin" && adminCount() <= 1) {
          toast("不能禁用最后一个管理员");
          return;
        }
        UI.confirm({
          title: "禁用账号",
          tone: "warn",
          icon: "warn",
          text: "禁用后「" + offUser.name + "」将无法登录，但历史问答记录里的姓名保留。",
          warn: "需要恢复时随时可以重新启用；如果账号已离职且确定不再使用，请改用「删除」。",
          okText: "确认禁用",
          okDanger: true,
        }).then(function (ok) {
          if (!ok) return;
          api.patchUser(offUser.id, { status: "disabled" })
            .then(function () {
              toast("已禁用「" + offUser.name + "」，记录保留");
              if (window.AdminLayout) window.AdminLayout.refreshUser();
              return refresh();
            })
            .catch(function (ex) { errorToast(ex, "禁用失败"); });
        });
        return;
      }

      if (on) {
        var onUser = findUser(on.getAttribute("data-on"));
        if (!onUser) return;
        api.patchUser(onUser.id, { status: "active" })
          .then(function () {
            toast("已启用「" + onUser.name + "」");
            return refresh();
          })
          .catch(function (ex) { errorToast(ex, "启用失败"); });
        return;
      }

      if (pwd) {
        var pwdUser = findUser(pwd.getAttribute("data-pwd"));
        if (!pwdUser) return;
        UI.confirm({
          title: "重置密码",
          tone: "warn",
          icon: "lock",
          text: "将为「" + pwdUser.name + "」（" + pwdUser.account + "）生成一次性临时密码，其当前密码立即失效。",
          note: "临时密码只在生成后显示一次；请通过可信渠道转交，本人首次登录后建议立即修改。",
          okText: "生成临时密码",
        }).then(function (ok) {
          if (!ok) return;
          api.resetPassword(pwdUser.id)
            .then(function (result) {
              openResetModal(pwdUser, (result && result.temporaryPassword) || "—");
            })
            .catch(function (ex) { errorToast(ex, "重置密码失败"); });
        });
        return;
      }

      if (del) {
        deleteUser(findUser(del.getAttribute("data-del")));
      }
    });

    function deleteUser(user) {
      if (!user) return;
      var me = currentUser();
      if (me && String(me.id) === String(user.id)) {
        toast("不能删除当前登录的账号");
        return;
      }
      if (user.role === "admin" && adminCount() <= 1) {
        toast("不能删除最后一个管理员");
        return;
      }
      UI.confirm({
        title: "删除账号",
        tone: "danger",
        icon: "trash",
        text: "即将删除账号「" + user.name + "」（" + user.account + " · "
          + (user.role === "admin" ? "管理员" : "普通用户") + "）。",
        warn: "本系统删除 = <b>软删除</b>：账号立即停用且不可恢复登录，历史问答记录中的姓名保留，"
          + "日志与评测追溯不受影响；账号会以「已禁用」留在列表里。<br/>"
          + "如果只是暂时停用（例如请假、调岗），请改用「禁用」——随时可以恢复。",
        okText: "确认删除",
        okDanger: true,
      }).then(function (ok) {
        if (!ok) return;
        api.removeUser(user.id)
          .then(function () {
            toast("已删除账号「" + user.name + "」（软删除，历史记录保留）");
            if (window.AdminLayout) window.AdminLayout.refreshUser();
            return refresh();
          })
          .catch(function (ex) { errorToast(ex, "删除失败"); });
      });
    }

    /* ---------------- 重置密码弹窗（一次性临时密码） ---------------- */
    function openResetModal(user, temporaryPassword) {
      $("pwdText").innerHTML = "已为 <b>" + esc(user.name) + "</b>（" + esc(user.account || "")
        + "）生成一次性临时密码：";
      $("pwdValue").textContent = temporaryPassword || "—";
      $("resetPwdModal").hidden = false;
    }

    $("btnCopyPwd").addEventListener("click", function () {
      var value = $("pwdValue").textContent;
      var done = function () { toast("临时密码已复制"); };
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(value).then(done, function () {
          toast("复制失败：请手动选中密码框复制（浏览器可能禁用了剪贴板）");
        });
      } else {
        toast("当前环境不支持自动复制：密码框已可全选，手动复制即可");
      }
    });

    /* ---------------- 新增 / 开通（同一个弹窗，两种模式） ---------------- */
    var addMode = "create";

    /* 密码框按需创建（2026-10 真实故障修复）。
     * 页面常驻 <input type="password"> 会让 Edge / Chrome 的密码管理器把本页当成登录表单，
     * 把保存的用户名（admin）以「自动填充预览」画进页面第一个文本框——也就是搜索框。
     * 这个预览不在 input.value 里，定时清空 value 治不了它；所以密码框只在弹窗打开期间
     * 存在于 DOM 中（关闭弹窗即移除），页面加载时不出现任何密码输入框。 */
    var passInput = null;
    function ensurePassInput() {
      if (passInput && passInput.parentNode) return passInput;
      passInput = document.createElement("input");
      passInput.id = "nuPass";
      passInput.type = "password";
      passInput.autocomplete = "new-password";
      passInput.placeholder = "至少 6 位，首次登录后由本人修改";
      $("nuPassField").appendChild(passInput);
      return passInput;
    }
    function dropPassInput() {
      if (passInput && passInput.parentNode) passInput.parentNode.removeChild(passInput);
      passInput = null;
    }
    /* 弹窗由 app.js 的委托统一关闭（hidden=true），这里跟着回收密码框 */
    var addUserModal = $("addUserModal");
    if (window.MutationObserver) {
      new MutationObserver(function () {
        if (addUserModal.hidden) dropPassInput();
      }).observe(addUserModal, { attributes: true, attributeFilter: ["hidden"] });
    }

    function showErr(message) {
      var box = $("nuErr");
      box.hidden = !message;
      if (message) box.querySelector("span").textContent = message;
    }

    function syncCred() {
      var local = $("nuCred").value === "local";
      var pass = ensurePassInput();
      pass.disabled = !local;
      pass.value = "";
      $("nuPassReq").textContent = local ? "*" : "（仅平台密码需要）";
      $("nuPassReq").style.color = local ? "#c0392b" : "#8a97a3";
      pass.placeholder = local ? "至少 6 位，首次登录后由本人修改" : "企业域账号无需平台密码";
      /* 开通模式下契约请求不带口令：本地账号由后端生成随机散列，随后自动重置为一次性口令 */
      $("nuPassField").hidden = addMode === "activate";
    }

    function openCreate(preAccount) {
      addMode = "create";
      $("addUserTitle").textContent = "新增用户";
      $("confirmAddUser").textContent = "创建账号";
      $("confirmAddUser").disabled = false;
      ensurePassInput().value = "";
      ["nuName", "nuAccount", "nuTeam"].forEach(function (id) { $(id).value = ""; });
      $("nuAccount").disabled = false;
      $("nuRole").value = "user";
      $("nuCred").value = "domain";
      if (preAccount) $("nuAccount").value = preAccount;
      $("nuPassField").hidden = false;
      $("nuHint").innerHTML = '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/>'
        + '<path d="M12 16v-4M12 8h.01"/></svg>'
        + "账号即工号：唯一、不可修改、不可复用。凭据来源选「企业域账号」时平台不保存密码；"
        + "选「平台密码」时才设初始密码，且可被管理员重置。";
      syncCred();
      showErr("");
      $("addUserModal").hidden = false;
    }

    function openActivate(item) {
      addMode = "activate";
      $("addUserTitle").textContent = "开通待开通工号";
      $("confirmAddUser").textContent = "开通账号";
      $("confirmAddUser").disabled = false;
      ensurePassInput().value = "";
      ["nuName", "nuTeam"].forEach(function (id) { $(id).value = ""; });
      $("nuAccount").value = item.account;
      $("nuAccount").disabled = true;
      $("nuRole").value = "user";
      $("nuCred").value = "domain";
      $("nuPassField").hidden = true;
      $("nuHint").innerHTML = '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/>'
        + '<path d="M12 16v-4M12 8h.01"/></svg>'
        + "该工号由宿主机声明过、平台还未开通。开通只补账号；历史会话归属保持「当时未识别」，"
        + "不会被追溯改写。凭据来源选「平台密码」时，开通后系统会生成一次性临时口令。";
      syncCred();
      showErr("");
      $("addUserModal").hidden = false;
    }

    $("btnAddUser").addEventListener("click", function () { openCreate(""); });
    $("nuCred").addEventListener("change", syncCred);

    $("claimBox").addEventListener("click", function (event) {
      var button = event.target.closest("[data-claim]");
      if (!button) return;
      var item = state.pending[Number(button.getAttribute("data-claim"))];
      if (item) openActivate(item);
    });

    $("confirmAddUser").addEventListener("click", function () {
      var name = $("nuName").value.trim();
      var account = $("nuAccount").value.trim();
      var credential = $("nuCred").value === "local" ? "local" : "domain";
      var role = $("nuRole").value === "admin" ? "admin" : "user";
      var team = $("nuTeam").value.trim() || null;
      var password = ensurePassInput().value;

      if (!name) { showErr("请填写姓名"); return; }
      if (!account) { showErr("请填写工号"); return; }
      if (addMode === "create") {
        if (!/^[A-Za-z0-9._-]{3,20}$/.test(account)) {
          showErr("工号用 3–20 位英文、数字、. _ -");
          return;
        }
        if (state.users.some(function (u) { return u.account.toLowerCase() === account.toLowerCase(); })) {
          showErr("工号「" + account + "」已存在，账号不可复用");
          return;
        }
        if (credential === "local" && password.length < 6) {
          showErr("平台密码至少 6 位");
          return;
        }
      }

      var button = $("confirmAddUser");
      button.disabled = true;

      if (addMode === "create") {
        api.createUser({
          account: account,
          name: name,
          role: role,
          credential: credential,
          team: team,
          password: credential === "local" ? password : null,
        }).then(function () {
          $("addUserModal").hidden = true;
          toast(credential === "local"
            ? "已创建账号「" + name + "」，首次登录后建议立即修改密码"
            : "已创建账号「" + name + "」，用企业域账号登录");
          return refresh();
        }).catch(function (ex) {
          button.disabled = false;
          showErr((ex && ex.message) || "创建账号失败");
        });
        return;
      }

      api.activatePending(account, {
        name: name,
        role: role,
        credential: credential,
        team: team,
      }).then(function (result) {
        $("addUserModal").hidden = true;
        toast("已开通账号「" + name + "」（" + account + "）");
        if (result && result.temporaryPassword) {
          openResetModal({ name: name, account: account }, result.temporaryPassword);
        }
        return refresh();
      }).catch(function (ex) {
        button.disabled = false;
        showErr((ex && ex.message) || "开通账号失败");
      });
    });

    /* ---------------- 筛选 / 搜索 / 刷新 ---------------- */
    $("roleTabs").addEventListener("click", function (event) {
      var button = event.target.closest("button[data-rt]");
      if (!button) return;
      state.roleFilter = button.getAttribute("data-rt");
      document.querySelectorAll("#roleTabs button").forEach(function (item) {
        item.classList.toggle("on", item === button);
      });
      renderTable();
    });

    var searchTimer = null;
    $("searchInput").addEventListener("input", function (event) {
      state.kw = event.target.value.trim().toLowerCase();
      clearTimeout(searchTimer);
      searchTimer = setTimeout(renderTable, 150);
    });

    /* 搜索框不保留上次输入：刷新 / 从 bfcache 返回后回到「全部账号」。
     * 真正会把 admin 画进搜索框的是 Edge 密码管理器的「自动填充预览」——
     * 它来自页面里常驻的密码框，已由 ensurePassInput 按需创建从源头解决。 */
    function clearSearch() {
      var input = $("searchInput");
      if (!input) return;
      input.value = "";
      state.kw = "";
    }
    clearSearch();
    window.addEventListener("pageshow", clearSearch);

    $("btnRefresh").addEventListener("click", function () {
      refresh().then(function () { toast("账号列表已刷新"); });
    });

    render();
    if (state.error) toast("数据加载失败：" + state.error);
  };
})();
