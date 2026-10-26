/*!
 * chat_widget loader：宿主页两行 script 接入，浮层 UI 跑在同源 iframe 里（样式隔离）。
 *
 *   <script src="/widget-app/loader.js" data-server="" data-position="right"></script>
 *
 * - data-server 留空 = 与宿主同源（M0 由 app 托管在 /widget-app）；
 * - 入口按钮/浮层外壳为固定尺寸（400×640、圆角 16、同款阴影），
 *   面板内部的 DOM/CSS/状态机在 iframe 里（widget.html + app.js）；
 * - 暴露 window.MoqianWidget.setContext(ctx, identity?) / open() / close() / reset()；
 * - 宿主上下文通过 postMessage 注入 iframe，Widget 不直接读宿主 DOM。
 */
(function () {
  'use strict';

  if (window.MoqianWidget) return; // 避免宿主页重复引入时叠加两个浮层

  var script = document.currentScript;
  var scriptSrc = script && script.src ? script.src : '';
  var scriptTag = script;
  if (!scriptSrc) {
    // 兼容异步/defer/打包注入等 currentScript 为空的场景：回退到文档里的 loader.js
    var found = document.querySelector('script[src*="loader.js"]');
    if (found) {
      scriptSrc = found.src;
      if (!scriptTag) scriptTag = found;
    }
  }
  // widget.html 与 loader.js 同目录；data-server 只决定 API 地址（默认同源）
  var base = scriptSrc ? scriptSrc.replace(/[^/]*$/, '') : location.origin + '/';
  var dataServer = scriptTag && scriptTag.getAttribute('data-server');
  var server = (dataServer || location.origin).replace(/\/$/, '');
  var position = (scriptTag && scriptTag.getAttribute('data-position')) === 'left' ? 'left' : 'right';
  /* 演示站入口码：宿主页 URL 的 ?code= 透传给 iframe，由 Widget 在建会话时带上。
     公开码写在官网链接里，等于公开——它只做分流与归因，成本由后端每日额度保证。 */
  var demoCode = '';
  try {
    demoCode = new URLSearchParams(location.search).get('code') || '';
  } catch (e) { /* 老浏览器忽略 */ }
  var frameUrl = base + 'widget.html?server=' + encodeURIComponent(server) +
    (demoCode ? '&code=' + encodeURIComponent(demoCode) : '');

  /* ---------- 注入浮层样式（在宿主页里自包含，不依赖宿主 CSS） ---------- */
  var STYLE_ID = 'moqian-widget-style';
  if (!document.getElementById(STYLE_ID)) {
    var style = document.createElement('style');
    style.id = STYLE_ID;
    style.textContent = [
      '#moqian-widget-root{position:fixed;z-index:2147483000;right:24px;bottom:24px;',
      'font-family:-apple-system,"Segoe UI","Microsoft YaHei",sans-serif}',
      '#moqian-widget-root.is-left{left:24px;right:auto}',
      '#moqian-widget-launcher{position:relative;width:56px;height:56px;padding:0;margin:0;',
      'border:0;border-radius:50%;background:#0f7a52;color:#fff;display:flex;align-items:center;',
      'justify-content:center;cursor:pointer;line-height:0;',
      'box-shadow:0 8px 24px rgba(15,122,82,.38);transition:background .15s ease,transform .15s ease}',
      '#moqian-widget-launcher:hover{background:#0b6040;transform:translateY(-2px)}',
      '#moqian-widget-launcher svg{width:26px;height:26px;fill:none;stroke:currentColor;',
      'stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round}',
      '#moqian-widget-dot{position:absolute;top:10px;right:12px;width:9px;height:9px;',
      'border-radius:50%;background:#ffd166;border:2px solid #0f7a52}',
      '#moqian-widget-panel{position:absolute;right:0;bottom:0;width:400px;height:640px;',
      'max-height:calc(100vh - 48px);border-radius:16px;overflow:hidden;background:#fff;',
      'box-shadow:0 12px 40px rgba(31,41,51,.18),0 2px 8px rgba(31,41,51,.08);display:none}',
      '#moqian-widget-root.is-left #moqian-widget-panel{right:auto;left:0}',
      '#moqian-widget-panel.is-open{display:block;animation:moqianWidgetPop .22s cubic-bezier(.2,.9,.3,1.2)}',
      '#moqian-widget-panel iframe{display:block;width:100%;height:100%;border:0;background:#fff}',
      '@keyframes moqianWidgetPop{from{opacity:0;transform:translateY(16px) scale(.97)}to{opacity:1;transform:none}}',
      '@media (max-width:560px){',
      '#moqian-widget-root{right:16px;bottom:16px}',
      '#moqian-widget-root.is-left{left:16px;right:auto}',
      '#moqian-widget-panel{position:fixed;top:0;left:0;right:0;bottom:0;width:100%;height:100%;',
      'max-height:none;border-radius:0}',
      '}',
      '@media (prefers-reduced-motion:reduce){#moqian-widget-panel.is-open{animation-duration:1ms}}'
    ].join('');
    document.head.appendChild(style);
  }

  /* ---------- 浮层入口 + iframe 外壳 ---------- */
  var root = document.createElement('div');
  root.id = 'moqian-widget-root';
  if (position === 'left') root.className = 'is-left';

  var launcher = document.createElement('button');
  launcher.id = 'moqian-widget-launcher';
  launcher.type = 'button';
  launcher.setAttribute('aria-label', '打开售后知识助手');
  launcher.innerHTML =
    '<svg viewBox="0 0 24 24" aria-hidden="true">' +
    '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>' +
    '</svg><span id="moqian-widget-dot"></span>';
  root.appendChild(launcher);

  var panel = document.createElement('div');
  panel.id = 'moqian-widget-panel';
  root.appendChild(panel);

  var frame = document.createElement('iframe');
  frame.title = '售后知识助手';
  frame.src = frameUrl;
  frame.setAttribute('allow', 'clipboard-write');
  panel.appendChild(frame);

  function mount() {
    if (document.body) {
      document.body.appendChild(root);
    } else {
      document.addEventListener('DOMContentLoaded', function () { document.body.appendChild(root); });
    }
  }
  mount();

  var state = { open: false };
  var pending = { context: null, identity: null };
  var ready = false;

  function send(msg) {
    if (!frame.contentWindow) return;
    frame.contentWindow.postMessage(msg, '*');
  }

  function flushPending() {
    if (!ready) return;
    if (pending.context !== null || pending.identity !== null) {
      send({
        type: 'moqian:context',
        context: pending.context || {},
        identity: pending.identity || null
      });
    }
    pending = { context: null, identity: null };
  }

  function setOpen(open) {
    state.open = open;
    panel.classList.toggle('is-open', open);
    launcher.style.display = open ? 'none' : 'flex';
    launcher.setAttribute('aria-expanded', open ? 'true' : 'false');
    if (open) send({ type: 'moqian:open' });
  }

  function paintBrand(color) {
    if (!/^#[0-9a-fA-F]{6}$/.test(color || '')) return;
    launcher.style.background = color;
    launcher.style.boxShadow = '0 8px 24px ' + hexToRgba(color, 0.38);
    var dot = document.getElementById('moqian-widget-dot');
    if (dot) dot.style.borderColor = color;
  }

  function hexToRgba(hex, alpha) {
    var m = /^#([0-9a-fA-F]{6})$/.exec(hex);
    if (!m) return 'rgba(15,122,82,' + alpha + ')';
    var n = parseInt(m[1], 16);
    return 'rgba(' + ((n >> 16) & 255) + ',' + ((n >> 8) & 255) + ',' + (n & 255) + ',' + alpha + ')';
  }

  launcher.addEventListener('click', function () { setOpen(!state.open); });

  window.addEventListener('message', function (event) {
    var data = event.data || {};
    if (data.type === 'moqian:ready') {
      ready = true;
      flushPending();
      if (state.open) send({ type: 'moqian:open' });
    } else if (data.type === 'moqian:close') {
      setOpen(false);
    } else if (data.type === 'moqian:brand') {
      paintBrand(data.color);
    } else if (data.type === 'moqian:handoff') {
      // P0：宿主只需知道"用户要转人工"；热线与上下文快照已经在 Widget 内展示。
      if (window.console && window.console.info) {
        window.console.info('[moqian-widget] handoff', data.payload);
      }
      try {
        document.dispatchEvent(new CustomEvent('moqian:handoff', { detail: data.payload }));
      } catch (e) { /* 老浏览器忽略 */ }
    } else if (data.type === 'moqian:resize') {
      // 预留：小屏时由 Widget 通知宿主让位；当前用 CSS 自适应
    }
  });

  window.MoqianWidget = {
    open: function () { setOpen(true); },
    close: function () { setOpen(false); },
    /**
     * @param {object} context  {ticketNo, deviceModel, deviceName, batch}
     * @param {{source:string, claimedEngineerId:string|null}} [identity]
     *        身份是"声明 + 在册校验"，后端会裁决；拿不到就跑匿名会话，不阻断问答。
     */
    setContext: function (context, identity) {
      pending = { context: context || {}, identity: identity || null };
      flushPending();
    },
    /** 重新加载 iframe = 新会话（清空对话与上下文），保留当前开合状态 */
    reset: function () {
      pending = { context: null, identity: null };
      ready = false;
      frame.src = frameUrl;
    },
    isOpen: function () { return state.open; }
  };
})();
