/* ============================================================
 * layout.js — 后台外壳（侧栏）单点维护 · 真实数据版
 *
 * 结构/样式/图标沿用早期原型，差异只在与后端
 * 相关的部分：
 *   1. 当前用户来自真实登录态（window.AdminAuth → GET /admin/me），
 *      不再有「演示身份」sessionStorage 角色；
 *   2. 导航只保留本批次已接入真实后端的页面（知识源管理 / 解析任务），
 *      未接入页面不渲染入口，也不放"假可用"的占位项；
 *   3. 去掉顶栏「重置数据」（真实数据没有可重置的演示会话）；
 *   4. 退出登录清除 localStorage token 并回登录页。
 *
 * 加载顺序：real-api.js → layout.js → app.js（本文件同步渲染，不依赖事件）。
 * 依赖：window.MOCK（real-api.js 提供）、window.AdminAuth、window.UI（可选）
 * ============================================================ */
(function () {
  "use strict";

  const ICONS = {
    brand: '<path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/>',
    sources: '<path d="M4 22h14a2 2 0 0 0 2-2V7l-5-5H6a2 2 0 0 0-2 2z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/><path d="M9 13h6M9 17h4"/>',
    jobs: '<path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M8 16H3v5"/>',
    settle: '<path d="m19 21-7-4-7 4V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2v16z"/>',
    log: '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>',
    gap: '<circle cx="12" cy="12" r="10"/><path d="M12 8v4M12 16h.01"/>',
    eval: '<path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/>',
    dashboard: '<path d="M3 3v18h18"/><path d="m19 9-5 5-4-4-3 3"/>',
    users: '<path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>',
    config: '<path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/><circle cx="12" cy="12" r="3"/>',
    account: '<path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/>',
    logout: '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><path d="m16 17 5-5-5-5"/><path d="M21 12H9"/>',
  };

  /* 品牌名从配置派生：改名只改一处（系统配置 → 品牌与外观） */
  function brandName() {
    const M = window.MOCK || {};
    const b = M.systemConfig && M.systemConfig.brand;
    return (b && b.systemName) || "知识助手管理台";
  }

  function isLogoImage(value) {
    return /^data:image\/(png|svg\+xml);base64,/.test(String(value || ""));
  }

  /* 自定义标识是 data URL（配置页在浏览器内校验后写入品牌配置）；
     未配置时继续用内置扳手图标，不请求额外静态资源。 */
  function logoMarkHtml(logo) {
    if (isLogoImage(logo)) {
      return '<img src="' + esc(logo) + '" alt="标识" />';
    }
    return '<svg viewBox="0 0 24 24" style="width:16px;height:16px">' + ICONS.brand + "</svg>";
  }

  function refreshBrand() {
    const title = document.querySelector(".side__title");
    if (title) title.textContent = brandName();
    const mark = document.querySelector(".side__brand .mark");
    const M = window.MOCK || {};
    const brand = (M.systemConfig && M.systemConfig.brand) || {};
    const color = brand.primaryColor;
    if (mark) {
      mark.innerHTML = logoMarkHtml(brand.logo);
      mark.style.background = isLogoImage(brand.logo) ? "#fff" : (color || "");
    }
    /* 主色必须作用于全局 CSS 变量，否则列表/标签/按钮只在配置页变色（D-012 修复） */
    if (color && window.UI && typeof window.UI.applyBrandTheme === "function") {
      window.UI.applyBrandTheme(color);
    }
    /* 品牌改了，页脚版权行（机构名 · 系统名称）跟着变 */
    paintFootBrand();
  }

  /* 当前登录账号：来自真实登录态（GET /admin/me 写入 localStorage） */
  function currentUser() {
    let me = null;
    try { me = window.AdminAuth && window.AdminAuth.user ? window.AdminAuth.user() : null; } catch (e) { me = null; }
    me = me || {};
    return {
      name: me.name || me.account || "管理员",
      account: me.account || "—",
      role: me.role === "user" ? "user" : "admin",
    };
  }
  function roleLabel(role) { return role === "user" ? "普通用户" : "管理员"; }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, (ch) => (
      { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[ch]
    ));
  }

  /* 统一确认浮层由 app.js 的 UI.confirm 提供；本文件先于 app.js 加载，
     所以只能在点击时取（同时保留原生兜底） */
  function confirmDialog(opts) {
    if (window.UI && window.UI.confirm) return window.UI.confirm(opts);
    return Promise.resolve(window.confirm((opts.title || "请确认") + "\n\n" + (opts.text || "")));
  }

  /* 导航结构：与原型分组一致；普通用户只看到「我的账号」，治理域对他们隐藏。
     page 对应 body[data-page]，用于选中态；badge 数字从真实统计派生。 */
  const NAV = [
    { group: "知识库" },
    { page: "sources", label: "知识源管理", href: "index.html", icon: "sources" },
    { page: "jobs", label: "解析任务", href: "jobs.html", icon: "jobs", badge: "jobs" },
    { page: "settle", label: "知识沉淀", href: "settle.html", icon: "settle", badge: "settle" },
    { group: "运营" },
    { page: "logs", label: "问答日志", href: "logs.html", icon: "log" },
    { page: "gaps", label: "知识缺口", href: "gaps.html", icon: "gap", badge: "gaps" },
    { page: "eval", label: "评测面板", href: "eval.html", icon: "eval" },
    { page: "dashboard", label: "主管驾驶舱", href: "dashboard.html", icon: "dashboard" },
    { group: "系统" },
    { page: "users", label: "用户管理", href: "users.html", icon: "users" },
    { page: "config", label: "系统配置", href: "config.html", icon: "config" },
    { group: "我的" },
    { page: "account", label: "我的账号", href: "account.html", icon: "account" },
    { page: "my-qa", label: "我的问答", href: "my-qa.html", icon: "log" },
    { page: "my-save", label: "我的收藏", href: "my-save.html", icon: "settle" },
  ];
  const SELF_PAGES = { account: true, "my-qa": true, "my-save": true };

  /* 切片预览是「知识源管理」的下级页：选中态挂在父项上 */
  const PAGE_ALIAS = { document: "sources" };

  /* 徽标数字：任务看板用运行中+排队中；沉淀/缺口用各自接口的全局计数。
     计数统一由 loadCounts() 拉取（不再依赖"某一页首屏恰好 bootstrap 过"）。 */
  function jobsInFlight() {
    const M = window.MOCK || {};
    const stats = M.STATS || {};
    const counts = M.jobCounts || stats.jobCounts || null;
    if (counts) return (counts.running || 0) + (counts.queued || 0);
    return (M.jobs || []).filter((j) => j.status === "running" || j.status === "queued").length;
  }

  function badgeCount(kind) {
    const M = window.MOCK || {};
    const stats = M.STATS || {};
    if (kind === "jobs") return jobsInFlight();
    if (kind === "settle") return Number(stats.settlePending || 0);
    if (kind === "gaps") return Number(stats.gapsOpen || 0);
    return 0;
  }

  function navHtml(role) {
    const visible = NAV.filter((n) => n.group || SELF_PAGES[n.page] || role === "admin");
    return visible.map((n, i) => {
      if (n.group) {
        const next = visible[i + 1];
        return next && !next.group ? `<div class="side__group">${n.group}</div>` : "";
      }
      const count = n.badge ? badgeCount(n.badge) : 0;
      const badge = count > 0 ? `<span class="side__badge">${count}</span>` : "";
      const nav = n.page ? ` data-nav="${n.page}"` : "";
      return `<a class="side__item"${nav} href="${n.href}">`
        + `<svg viewBox="0 0 24 24">${ICONS[n.icon]}</svg><span>${n.label}</span>${badge}</a>`;
    }).join("");
  }

  function render(host) {
    const me = currentUser();
    const roleText = roleLabel(me.role);
    const M = window.MOCK || {};
    const brand = (M.systemConfig && M.systemConfig.brand) || {};
    const markBg = isLogoImage(brand.logo) ? ' style="background:#fff"' : "";
    host.innerHTML = `
      <div class="side__brand">
        <span class="mark"${markBg}>${logoMarkHtml(brand.logo)}</span>
        <span class="side__title">${esc(brandName())}</span>
      </div>
      <nav>${navHtml(me.role)}</nav>
      <div class="side__foot">
        <div class="side__me">
          <span class="ava">${esc(me.name.slice(0, 1))}</span>
          <span class="side__me-txt"><b>${esc(me.name)}</b><i>${roleText}</i></span>
        </div>
        <button class="side__logout" id="sideLogout" type="button" title="退出登录，回到登录页">
          <svg viewBox="0 0 24 24">${ICONS.logout}</svg><span>退出</span>
        </button>
      </div>`;
  }

  function wire(host) {
    // 1. 选中态（下级页映射到父项）
    const rawPage = document.body.dataset.page;
    const page = PAGE_ALIAS[rawPage] || rawPage;
    host.querySelectorAll(".side__item[data-nav]").forEach((a) => {
      if (a.dataset.nav === page) a.classList.add("active");
    });

    // 2. 折叠开关（初始状态由 syncCollapse 统一决定：宽屏用图标条，≤900 用抽屉）
    const brand = host.querySelector(".side__brand");
    const toggle = document.createElement("button");
    toggle.className = "side__toggle";
    toggle.type = "button";
    toggle.title = "折叠 / 展开导航";
    toggle.innerHTML = '<svg viewBox="0 0 24 24"><path d="M4 6h16M4 12h16M4 18h16"/></svg>';
    brand.appendChild(toggle);
    toggle.addEventListener("click", () => {
      const collapsed = document.body.classList.toggle("side-collapsed");
      localStorage.setItem("admin_side_collapsed", collapsed ? "1" : "0");
    });

    // 3. 折叠态 tooltip
    host.querySelectorAll(".side__item").forEach((a) => {
      const label = a.querySelector("span") ? a.querySelector("span").textContent.trim() : "";
      if (label) a.setAttribute("data-tip", label);
    });

    // 4. 退出登录：清除本机 JWT，回登录页（真实会话注销 = 丢弃 token）
    const logout = document.getElementById("sideLogout");
    if (logout) {
      logout.addEventListener("click", () => {
        confirmDialog({
          title: "退出登录",
          tone: "normal",
          icon: "logout",
          text: "退出后回到登录页。",
          note: "登录态保存在本机浏览器（localStorage）；下次进入需要重新登录。",
          okText: "退出登录",
        }).then((ok) => {
          if (!ok) return;
          if (window.AdminAuth) window.AdminAuth.clear();
          window.location.href = "login.html";
        });
      });
    }
  }

  /* 移动端（≤900px）：侧栏改抽屉 */
  function initResponsive(host) {
    const scrim = document.createElement("div");
    scrim.className = "nav-scrim";
    document.body.appendChild(scrim);

    const topbar = document.querySelector(".topbar");
    if (topbar) {
      const btn = document.createElement("button");
      btn.className = "nav-toggle";
      btn.type = "button";
      btn.title = "打开导航";
      btn.setAttribute("aria-label", "打开导航");
      btn.innerHTML = '<svg viewBox="0 0 24 24"><path d="M4 6h16M4 12h16M4 18h16"/></svg>';
      topbar.insertBefore(btn, topbar.firstChild);
      btn.addEventListener("click", () => document.body.classList.toggle("nav-open"));
    }

    const close = () => document.body.classList.remove("nav-open");
    scrim.addEventListener("click", close);
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") close(); });
    host.querySelectorAll(".side__item").forEach((a) => a.addEventListener("click", close));

    const mq = window.matchMedia("(max-width: 900px)");
    function syncCollapse() {
      if (mq.matches) {
        document.body.classList.remove("side-collapsed");
      } else if (localStorage.getItem("admin_side_collapsed") === "1") {
        document.body.classList.add("side-collapsed");
      }
    }
    if (mq.addEventListener) mq.addEventListener("change", syncCollapse);
    else if (mq.addListener) mq.addListener(syncCollapse);
    syncCollapse();
  }

  /* 当前账号变了（重新登录）时，侧栏底部要立刻换人 */
  function refreshUser() {
    const box = document.querySelector(".side__me");
    if (!box) return;
    const me = currentUser();
    box.innerHTML = `<span class="ava">${esc(me.name.slice(0, 1))}</span>`
      + `<span class="side__me-txt"><b>${esc(me.name)}</b><i>${roleLabel(me.role)}</i></span>`;
  }

  /* 页面内数据刷新后，侧栏徽标与顶栏任务提示都要跟着变；
     数字来自接口而非"当前页恰好加载过的列表"，跨页一致。 */
  function paintJobsPill() {
    const right = document.querySelector(".topbar__right");
    if (!right) return;
    let pill = right.querySelector(".topbar__jobs");
    if (!pill) {
      pill = document.createElement("a");
      pill.className = "topbar__pill topbar__jobs";
      pill.href = "jobs.html";
      pill.title = "解析任务：运行中 + 排队中，点击去任务看板";
      right.insertBefore(pill, right.firstChild);
    }
    const count = jobsInFlight();
    pill.style.display = count > 0 ? "" : "none";
    pill.classList.toggle("is-busy", count > 0);
    if (count > 0) {
      pill.innerHTML = '<svg viewBox="0 0 24 24">' + ICONS.jobs + "</svg>"
        + "<span>" + count + " 个任务进行中</span>";
    }
  }

  function paintBadges() {
    const items = NAV.filter((n) => n.page);
    document.querySelectorAll(".side__item[data-nav]").forEach((a) => {
      const item = items.filter((n) => n.page === a.dataset.nav)[0];
      if (!item || !item.badge) return;
      const count = badgeCount(item.badge);
      let el = a.querySelector(".side__badge");
      if (count > 0) {
        if (!el) {
          el = document.createElement("span");
          el.className = "side__badge";
          a.appendChild(el);
        }
        el.textContent = count;
      } else if (el) {
        el.parentNode.removeChild(el);
      }
    });
    paintJobsPill();
  }

  /* 全局计数：任务（运行中+排队中）/ 待确认沉淀 / 未覆盖缺口。
     三个都是小请求（pageSize=1），用于让徽标在任何页面都真实；
     角色不是管理员时不发请求（治理接口会 403）。 */
  function loadCounts() {
    const me = currentUser();
    if (me.role !== "admin"
      || !window.AdminAPI || typeof window.AdminAPI.request !== "function") {
      paintBadges();
      return;
    }
    const api = window.AdminAPI;
    const M = window.MOCK || (window.MOCK = {});
    M.STATS = M.STATS || {};

    api.request("GET", "/jobs", { query: { page: 1, pageSize: 1, withStats: "true" } })
      .then((data) => {
        const counts = data && data.stats && data.stats.counts;
        if (counts) {
          M.jobCounts = counts;
          M.STATS.jobCounts = counts;
        }
      })
      .catch(() => { /* 计数失败保持旧值，不打断页面 */ })
      .then(paintBadges);

    api.request("GET", "/settlements", { query: { page: 1, pageSize: 1, withStats: "true" } })
      .then((data) => {
        if (data && data.stats) M.STATS.settlePending = Number(data.stats.pending || 0);
      })
      .catch(() => {})
      .then(paintBadges);

    api.request("GET", "/gaps", { query: { status: "open", page: 1, pageSize: 1 } })
      .then((data) => {
        if (data && data.pagination) M.STATS.gapsOpen = Number(data.pagination.total || 0);
      })
      .catch(() => {})
      .then(paintBadges);
  }

  function refreshBadges() {
    loadCounts();
  }
  window.AdminLayout = { refreshBadges: refreshBadges, refreshBrand: refreshBrand, refreshUser: refreshUser };

  /* 品牌/自测入口（D-012）：从 /admin/config 读取已保存配置；
     品牌色作用于全局 CSS 变量，自测入口按预览开关即时显隐。 */
  function loadBrandFromConfig() {
    if (!window.AdminAPI || typeof window.AdminAPI.request !== "function") return;
    window.AdminAPI.request("GET", "/config").then(function (cfg) {
      if (!cfg) return;
      if (cfg.brand) {
        window.MOCK = window.MOCK || {};
        window.MOCK.systemConfig = window.MOCK.systemConfig || {};
        window.MOCK.systemConfig.brand = cfg.brand;
        if (cfg.widget) window.MOCK.systemConfig.widget = cfg.widget;
        refreshBrand();
        if (window.UI && typeof window.UI.applyBrandTheme === "function") {
          window.UI.applyBrandTheme(cfg.brand.primaryColor, true);
        }
        refreshBadges();
      }
      if (cfg.preview && window.AdminPreview && typeof window.AdminPreview.setEnabled === "function") {
        window.AdminPreview.setEnabled(!!cfg.preview.enabled);
      }
    }).catch(function () { /* 配置不可读时保持默认品牌与入口 */ });
  }

  /* 版本号：现在只在**右侧页脚**出现（侧栏底部不再挂版本）。
    数据取 /health（公开、不依赖数据库、不带任何密钥）：
       · 页脚里的 [data-app-version] 显示版本 + 短 SHA；
       · 同一响应里的 envLabel（本地环境 / 生产环境）填到页脚与环境标识；
       · 失败就显示「版本未知」——宁可难看，也不要显示一个猜的版本号。 */
  /* 版权行：© {年份} {机构名} · {系统名称} —— 两个变量都来自「品牌与外观」，
     改品牌后不用改代码（refreshBrand 会重画页脚）。 */
  function footBrandText() {
    const M = window.MOCK || {};
    const brand = (M.systemConfig && M.systemConfig.brand) || {};
    const org = brand.orgName || "陌阡科技";
    const system = brand.systemName || brandName();
    return "© " + new Date().getFullYear() + " " + org + " · " + system;
  }

  function paintFootBrand() {
    const node = document.querySelector("[data-foot-brand]");
    if (node) node.textContent = footBrandText();
  }

  function renderFooter() {
    const main = document.querySelector(".main");
    if (!main || main.querySelector(".page-foot")) return;
    const foot = document.createElement("footer");
    foot.className = "page-foot";
    foot.innerHTML =
      '<span data-foot-brand></span>'
      + '<span class="page-foot__sep">·</span>'
      + '<span data-app-env title="由 .env 的 APP_ENV_LABEL 决定">—</span>'
      + '<span class="page-foot__sep">·</span>'
      + '<span data-app-version title="当前运行版本">版本未知</span>';
    main.appendChild(foot);
    paintFootBrand();
  }

  /* 顶栏「本机环境 · …」里的环境名换成配置里的（本地环境 / 生产环境）。
     只替换文本节点里的那四个字，不动图标与其余文案。 */
  function applyEnvLabel(label) {
    if (!label) return;
    document.querySelectorAll(".topbar__pill, .login-foot").forEach(function (node) {
      node.childNodes.forEach(function (child) {
        if (child.nodeType === 3 && child.nodeValue.indexOf("本机环境") >= 0) {
          child.nodeValue = child.nodeValue.replace("本机环境", label);
        }
      });
    });
  }

  function loadVersion() {
    const nodes = document.querySelectorAll("[data-app-version]");
    const envNodes = document.querySelectorAll("[data-app-env]");
    if (!nodes.length && !envNodes.length) return;
    fetch("/health", { headers: { Accept: "application/json" } })
      .then(function (res) {
        if (!res.ok) throw new Error("HTTP " + res.status);
        return res.json();
      })
      .then(function (data) {
        const ver = data && data.version ? String(data.version) : "";
        const sha = data && data.buildSha ? String(data.buildSha).slice(0, 7) : "";
        const text = ver ? "v" + ver + (sha ? " · " + sha : "") : "版本未知";
        const title = ver
          ? "版本 v" + ver
            + (sha ? "\n提交 " + sha : "")
            + (data.buildTime ? "\n构建 " + data.buildTime : "")
          : "读到 /health 了，但响应里没有版本号";
        nodes.forEach(function (n) {
          n.textContent = text;
          n.title = title;
        });
        const label = data && data.envLabel ? String(data.envLabel) : "";
        envNodes.forEach(function (n) { n.textContent = label || "环境未知"; });
        applyEnvLabel(label);
      })
      .catch(function () {
        nodes.forEach(function (n) {
          n.textContent = "版本未知";
          n.title = "读取 /health 失败：后端可能未启动或接口被拦截";
        });
      });
  }

  const host = document.getElementById("appSide");
  if (host) {
    render(host);
    wire(host);
    initResponsive(host);
    paintBadges();
    loadCounts();
    renderFooter();
    /* 顶栏的版本胶囊并入页脚：版本只在一个地方出现，避免两处不同步 */
    document.querySelectorAll(".topbar__pill--ver").forEach(function (n) { n.remove(); });
    loadVersion();
    loadBrandFromConfig();
    /* 任务数会随 worker 推进变化：60s 轮询一次（页面隐藏时跳过，避免无谓请求） */
    if (currentUser().role === "admin") {
      window.setInterval(function () {
        if (!document.hidden) loadCounts();
      }, 60000);
    }
  }
})();
