/* ============================================================
 * account.js — 我的账号（个人域，PRD FR-P1 / §2.3 / admin spec §4.12）
 *
 * 为什么要有这一页：普通用户有账号，就必须能看见"我是谁"、能改自己的密码、
 * 能看见自己收藏的结果——否则"临时口令首次登录须改密"这条策略无处执行。
 * 但这里刻意只做只读视图 + 改密，不做第二个提问入口（不新建系统是产品纪律）。
 *
 * 数据源：window.UsersApi（真实接口 GET /admin/me）；问答/收藏已拆到 my-qa.html / my-save.html，
 * 本文件只做渲染与交互，不发请求、不造数据。
 * ============================================================ */
(function () {
  "use strict";
  window.AdminPages = window.AdminPages || {};

  window.AdminPages.account = function () {
    var UI = window.UI;
    var api = window.UsersApi;
    if (!UI || !api) return;

    var boot = api.boot("account");
    var ME = (api.me && api.me()) || {};
    var esc = UI.esc;
    var toast = UI.toast;

    var ROLE = { admin: "管理员", user: "普通用户" };
    var CRED = {
      domain: "企业域账号（平台不保存密码，也不能在此重置）",
      local: "平台密码（忘记密码请找管理员重置）",
    };
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
    var PAGE_NAME = {
      sources: "知识源管理", document: "切片预览", jobs: "解析任务", settle: "知识沉淀",
      logs: "问答日志", gaps: "知识缺口", eval: "评测面板", dashboard: "主管驾驶舱",
      users: "用户管理", config: "系统配置",
    };

    var $ = function (id) { return document.getElementById(id); };

    /* ---------------- 越权提示（治理域页面无权限时跳回本页并带 ?denied=） ---------------- */
    function renderDenied() {
      var bar = $("deniedBar");
      var text = $("deniedText");
      var denied = null;
      try { denied = new URLSearchParams(location.search).get("denied"); } catch (e) { denied = null; }
      if (!denied) return;
      bar.hidden = false;
      text.textContent = "「" + (PAGE_NAME[denied] || denied) + "」仅管理员可见，已回到「我的账号」。"
        + "知识治理、日志与配置属于治理域，普通用户只有个人域。";
    }

    /* ---------------- 账号信息（与用户管理里同一人完全一致，字段来自 /admin/me） ---------------- */
    function renderMe() {
      var rows = [
        ["姓名", ME.name || "—"],
        ["工号", (ME.account || "—") + "（账号即工号，全平台唯一、不可修改）"],
        ["角色", ROLE[ME.role] || "普通用户"],
        ["小组", ME.team || "—"],
        ["状态", ME.status === "disabled" ? "已禁用" : "启用中"],
        ["凭据来源", CRED[ME.credential] || CRED.local],
        ["最近活跃", ME.lastActive || "—"],
      ];
      $("meRows").innerHTML = rows.map(function (row) {
        return '<tr><td style="width:150px;color:#52606d">' + esc(row[0]) + "</td>"
          + '<td style="font-weight:' + (row[0] === "姓名" ? "600" : "400") + '">'
          + esc(row[1]) + "</td></tr>";
      }).join("");
    }

    /* ---------------- 身份来源说明 ----------------
     * 后台登录态是平台 JWT；工程师端身份来源（网关 SSO / 宿主注入 / 密码登录）的
     * 优先级与可识别率阈值属于「系统配置」治理域，本页只保留"我是谁 + 这页看到什么"，
     * 不复制那组数字、也不与页脚提示说同一句话。 */
    function renderIdentity() {
      var credential = ME.credential === "domain" ? "企业域账号" : "平台密码";
      $("idBox").innerHTML =
        '<div style="font-size:12px;color:#52606d;line-height:1.9">'
        + "本后台登录身份：<b>" + credential + "</b>（工号 " + esc(ME.account || "—") + "）。<br/>"
        + "工程师端身份是「<b>声明 + 在册校验</b>」：宿主机上报工号，平台只确认它在册，"
        + "不验证操作者是否本人，也<b>不用于授权</b>。<br/>"
        + "现场启用哪些来源、按什么优先级、可识别率阈值多少，统一在「系统配置 → 身份接入」查看。"
        + '<div class="hint" style="margin-top:8px"><svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/>'
        + '<path d="M12 16v-4M12 8h.01"/></svg>越靠前的来源越省事：网关 SSO / 宿主注入都不需要工程师操作；'
        + "都拿不到时降级为「未识别」——问答照常可用，只是不计入按人指标，也不会出现在本页。"
        + "</div>"
        + "</div>";
    }

    /* ---------------- 安全与登录 ---------------- */
    function renderSecurity() {
      var box = $("pwdBox");
      var button = $("btnPwd");

      if (ME.credential === "domain") {
        /* 域账号不显示「修改密码」按钮：点了没反应的死按钮比没有更糟 */
        button.hidden = true;
        button.disabled = true;
        button.title = "密码由企业域账号管理，本平台不保存密码";
        box.innerHTML =
          '<div style="font-size:12px;color:#52606d;line-height:1.9">'
          + "<b>密码由企业域账号管理</b>：本平台不保存、也不代管你的密码，所以这里没有「修改密码」。<br/>"
          + "要改密码，请在公司统一认证 / 域账号里操作，改完这里的登录立即生效。"
          + "<br/><b>忘记密码或账号被锁</b>：请联系公司 IT / 统一认证管理员——平台无法代为重置。"
          + '<div class="hint" style="margin-top:8px"><svg viewBox="0 0 24 24">'
          + '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>'
          + "这是安全收益：离职停用、密码策略都跟着域走，平台不另存一份。</div></div>";
        return;
      }

      button.hidden = false;
      button.disabled = false;
      button.title = "修改本人平台密码";
      box.innerHTML =
        '<div style="font-size:12px;color:#52606d;line-height:1.9">'
        + "当前使用<b>平台密码</b>登录。忘记密码时，管理员可为你生成一次性临时密码。"
        + "</div>"
        + '<div class="pwd-form" id="pwdForm" hidden>'
        + '<input type="password" id="pwOld" placeholder="当前密码" autocomplete="current-password" />'
        + '<input type="password" id="pwNew" placeholder="新密码（至少 6 位）" autocomplete="new-password" />'
        + '<input type="password" id="pwNew2" placeholder="再输入一次新密码" autocomplete="new-password" />'
        + '<div class="login-err" id="pwErr"></div>'
        + '<button class="btn btn--primary btn--sm" id="pwSave" type="button">保存新密码</button>'
        + "</div>";

      var form = $("pwdForm");
      button.addEventListener("click", function () { form.hidden = !form.hidden; });

      $("pwSave").addEventListener("click", function () {
        var oldPassword = $("pwOld").value.trim();
        var first = $("pwNew").value.trim();
        var second = $("pwNew2").value.trim();
        var err = $("pwErr");
        var fail = function (message) { err.textContent = message; };
        if (!oldPassword) return fail("请输入当前密码");
        if (first.length < 6) return fail("新密码至少 6 位");
        if (first !== second) return fail("两次输入的新密码不一致");
        if (first === oldPassword) return fail("新密码不能与当前密码相同");
        err.textContent = "";

        var saveButton = $("pwSave");
        saveButton.disabled = true;
        api.changePassword(oldPassword, first)
          .then(function () {
            ["pwOld", "pwNew", "pwNew2"].forEach(function (id) { $(id).value = ""; });
            form.hidden = true;
            saveButton.disabled = false;
            toast("密码已修改，下次登录请使用新密码");
          })
          .catch(function (ex) {
            saveButton.disabled = false;
            if (ex && ex.status === 401) return;
            fail((ex && ex.message) || "修改密码失败，请稍后再试");
          });
      });
    }

    renderDenied();
    renderMe();
    renderIdentity();
    renderSecurity();
    if (boot.error) toast("数据加载失败：" + boot.error);
  };
})();
