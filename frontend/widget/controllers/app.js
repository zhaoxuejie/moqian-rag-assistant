/* ============================================================
 * app.js — Widget 状态与渲染（真实后端版）
 *
 * 来源：状态机与全部渲染结构移植自早期原型（验收基准），
 * 只把 mock 数据层换成真实接口：
 *   - 配置/欢迎语/示例/品牌色 ← GET /widget/config（行业词不写死）
 *   - 身份/宿主上下文       ← POST /widget/session（声明 + 在册校验）
 *   - 问答                  ← POST /chat SSE（structured/steps/citations/
 *                              conflict/gap/safe/error/done；ask 见 D-005）
 *   - 引用原文              ← GET /widget/evidence/{id}（展开时按需取）
 *   - 反馈 / 转人工         ← POST /widget/feedback、/widget/handoff
 *
 * 已知后端缺口（前端不造假数据，只保留原型 UI 结构）：
 *   D-005：后端在"型号已知但批次缺失、且问题批次敏感"时产生 `ask` 追问事件；前端
 *          renderAsk / pickClarify 按契约处理，选定后带 clarify 再请求一次。
 *   D-004：断网时主动中止 SSE；显示"连接中断"专用卡，网络恢复后自动重试一次。不续传。 
 * ============================================================ */
(function () {
  'use strict';

  var params = new URLSearchParams(location.search);
  window.WidgetAPI.setServer(params.get('server') || '');
  /* 演示站入口码：loader 会把宿主页 URL 的 ?code= 透传进来（独立打开 widget.html 也支持） */
  var DEMO_CODE = params.get('code') || '';

  /* ---------- 内联 Lucide 图标（与原型同源，零依赖） ---------- */
  var I = {
    wrench: '<svg viewBox="0 0 24 24"><path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/></svg>',
    file: '<svg viewBox="0 0 24 24"><path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/><path d="M10 9H8M16 13H8M16 17H8"/></svg>',
    ticket: '<svg viewBox="0 0 24 24"><rect width="8" height="4" x="8" y="2" rx="1"/><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/><path d="M12 11h4M12 16h4M8 11h.01M8 16h.01"/></svg>',
    shield: '<svg viewBox="0 0 24 24"><path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z"/><path d="M12 8v4M12 16h.01"/></svg>',
    alert: '<svg viewBox="0 0 24 24"><path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z"/><path d="M12 9v4M12 17h.01"/></svg>',
    check: '<svg viewBox="0 0 24 24"><path d="M20 6 9 17l-5-5"/></svg>',
    checkCircle: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><path d="m9 12 2 2 4-4"/></svg>',
    xCircle: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><path d="m15 9-6 6M9 9l6 6"/></svg>',
    bookmark: '<svg viewBox="0 0 24 24"><path d="m19 21-7-4-7 4V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2v16z"/></svg>',
    chev: '<svg viewBox="0 0 24 24"><path d="m6 9 6 6 6-6"/></svg>',
    filter: '<svg viewBox="0 0 24 24"><polygon points="22 3 2 3 10 12.46 10 19 14 21 14 12.46 22 3"/></svg>',
    retry: '<svg viewBox="0 0 24 24"><path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M8 16H3v5"/></svg>',
    headphone: '<svg viewBox="0 0 24 24"><path d="M3 14h3a2 2 0 0 1 2 2v3a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-5a9 9 0 0 1 18 0v5a2 2 0 0 1-2 2h-1a2 2 0 0 1-2-2v-3a2 2 0 0 1 2-2h3"/></svg>',
    zap: '<svg viewBox="0 0 24 24"><path d="M4 14a1 1 0 0 1-.78-1.63l9.9-10.2a.5.5 0 0 1 .86.46l-1.92 6.02A1 1 0 0 0 13 10h7a1 1 0 0 1 .78 1.63l-9.9 10.2a.5.5 0 0 1-.86-.46l1.92-6.02A1 1 0 0 0 11 14z"/></svg>',
    paperclip: '<svg viewBox="0 0 24 24"><path d="m21.44 11.05-9.19 9.19a6 6 0 0 1-8.49-8.49l8.57-8.57A4 4 0 1 1 18 8.84l-8.59 8.57a2 2 0 0 1-2.83-2.83l8.49-8.48"/></svg>'
  };

  /* ---------- 状态 ---------- */
  var state = {
    config: null,        // GET /widget/config
    identity: { source: 'anonymous', claimedEngineerId: null },
    engineer: null,      // POST /widget/session 返回的 engineer
    hostContext: {},     // 宿主注入（postMessage）或 URL 参数
    sessionId: null,
    bootFail: false,     // 助手服务不可达（冷启动/重试仍失败）
    gateBlocked: false,  // 演示入口码缺失/无效：只提示入口，不当作服务故障
    offline: false,      // 浏览器真实断网（D-004：断流即中止，恢复后自动重试一次）
    sending: false,
    abort: null,         // 当前 /chat 的 AbortController（断网时主动中止流）
    pendingRetry: null,  // {text, clarify, node}：断流后等待 online 自动重试一次
    turnsLeft: null,     // 演示会话剩余轮次（null = 非演示站，不显示这一行）
    msgCount: 0,         // 本轮会话消息数（仅用于界面提示，转人工统计走后端）
    lastMessageId: null, // done / gap / safe 返回的本轮消息 id
    citeOrder: [],       // 引用角标编号：按步骤首次出现顺序
    citations: {},       // evidenceId -> Citation（来自 citations 事件）
    evidenceCache: {},   // evidenceId -> Evidence（原文，按需 GET）
    clarified: {}        // 追问选择缓存（同一问题只追问一次，D-005）
  };

  /* ---------- DOM ---------- */
  function $(id) { return document.getElementById(id); }
  var wBody = $('wBody'), wInput = $('wInput'), wSend = $('wSend');
  var wNetbar = $('wNetbar'), wNetText = $('wNetText'), wCtxLine = $('wCtxLine');
  var wIdTag = $('wIdTag'), wIdNote = $('wIdNote'), wIdNoteText = $('wIdNoteText');
  var wToasts = $('wToasts'), wHandoffHead = $('wHandoffHead'), wClose = $('wClose');
  var wQuota = $('wQuota');
  var modal = $('wHandoffModal'), wCarryList = $('wCarryList');
  var wHotline = $('wHotline'), wHotlineMeta = $('wHotlineMeta'), wHotlineNote = $('wHotlineNote');

  /* ---------- 小工具 ---------- */
  function el(tag, cls, html) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (html !== undefined) n.innerHTML = html;
    return n;
  }
  /* 流式呈现期间跟随到底部（与原型"逐条呈现、始终看最新"一致）；
     用户主动上翻（滚轮/触摸）才暂停跟随，下一条问题再恢复。
     注意不能用 scroll 事件判断：scroll-behavior:smooth 的程序化滚动也会触发 scroll，
     会把"跟随"误判成"用户上翻"。 */
  var autoFollow = true;
  var activeTypers = 0;
  function scrollBottom() { wBody.scrollTop = wBody.scrollHeight; }
  function pauseFollow() { autoFollow = false; }
  wBody.addEventListener('wheel', pauseFollow, { passive: true });
  wBody.addEventListener('touchmove', pauseFollow, { passive: true });
  function toast(html) {
    var t = el('div', 'w-toast', html);
    wToasts.appendChild(t);
    setTimeout(function () { t.style.opacity = '0'; t.style.transition = 'opacity .3s'; }, 2400);
    setTimeout(function () { t.remove(); }, 2800);
  }
  function escapeHtml(value) {
    return String(value === null || value === undefined ? '' : value)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }
  function textHtml(value) {
    return escapeHtml(value).replace(/\n/g, '<br>');
  }
  function reduceMotion() {
    return !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
  }
  function escapeRegExp(value) {
    return String(value).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  }

  /* 发送按钮可用性：空内容 / 发送中 / 服务不可达 / 断网 一律置灰（spec §4.2）。
     D-004：这里不再有"暂存待重发"状态（后端不支持断线续传），断网只提示。 */
  function syncSendState() {
    var empty = !wInput.value.trim();
    var blocked = state.bootFail || state.gateBlocked;
    wSend.disabled = state.sending || empty || blocked || state.offline;
    wInput.disabled = blocked;
    wSend.title = state.offline ? '网络已断开'
      : (state.gateBlocked ? '需要演示入口码'
        : (state.bootFail ? '助手服务不可用' : '发送'));
  }

  /* ---------- 品牌色：由 /widget/config 注入（纪律 6），并派生浅色 ---------- */
  function hexToRgb(hex) {
    var m = /^#([0-9a-fA-F]{6})$/.exec(hex || '');
    if (!m) return null;
    var n = parseInt(m[1], 16);
    return { r: (n >> 16) & 255, g: (n >> 8) & 255, b: n & 255 };
  }
  function toHex(rgb) {
    function part(n) {
      var v = Math.max(0, Math.min(255, Math.round(n))).toString(16);
      return v.length === 1 ? '0' + v : v;
    }
    return '#' + part(rgb.r) + part(rgb.g) + part(rgb.b);
  }
  function mixColor(hex, targetHex, ratio) {
    var a = hexToRgb(hex), b = hexToRgb(targetHex);
    if (!a || !b) return hex;
    return toHex({
      r: a.r + (b.r - a.r) * ratio,
      g: a.g + (b.g - a.g) * ratio,
      b: a.b + (b.b - a.b) * ratio
    });
  }
  function applyBrand(hex) {
    if (!hexToRgb(hex)) return;
    var style = document.documentElement.style;
    style.setProperty('--green', hex);
    style.setProperty('--green-dark', mixColor(hex, '#000000', 0.2));
    style.setProperty('--green-soft', mixColor(hex, '#ffffff', 0.92));
    style.setProperty('--green-line', mixColor(hex, '#ffffff', 0.72));
  }
  function notifyBrand() {
    if (window.parent !== window && state.config && state.config.brandColor) {
      window.parent.postMessage({ type: 'moqian:brand', color: state.config.brandColor }, '*');
    }
  }
  function applyFeatures() {
    var features = (state.config && state.config.features) || {};
    if (features.handoff === false) {
      wHandoffHead.hidden = true;
    } else {
      wHandoffHead.hidden = false;
    }
  }

  /* ---------- 身份标签与未识别提示（PRD §2.3） ---------- */
  function renderIdentity() {
    var id = state.engineer;
    if (!id) {
      wIdTag.textContent = '识别中…';
      wIdTag.classList.remove('is-anon');
      wIdNote.hidden = true;
      return;
    }
    wIdTag.textContent = id.identified
      ? (id.name + ' · 已识别')
      : (id.claimed ? '未开通 · ' + id.claimed : '未识别身份');
    wIdTag.classList.toggle('is-anon', !id.identified);
    wIdTag.title = id.identified
      ? '身份来源：' + (id.sourceLabel || id.source) + '（工号 ' + id.account + '）'
      : '身份是声明 + 在册校验，不是认证；请用企业账号登录后再点收藏，才会归到你名下';

    if (id.identified) {
      wIdNote.hidden = true;
      return;
    }
    wIdNote.hidden = false;
    wIdNoteText.textContent = id.claimed
      ? '工号 ' + id.claimed + ' 尚未开通，本次不计入个人统计；问答与转人工照常可用。'
      : '本次访问未识别身份，问答可以正常使用，但不会计入个人统计与「我的收藏」。需要归属时请用企业账号登录。';
  }

  /* ---------- 上下文提示行 ---------- */
  function renderCtxLine() {
    var ctx = state.hostContext || {};
    var loaded = !!(ctx.ticketNo || ctx.deviceModel || ctx.batch);
    wCtxLine.textContent = loaded ? '已载入当前工单上下文' : '未检测到设备上下文 · 全库检索';
  }

  /* ---------- 欢迎屏（欢迎语/示例来自 /widget/config） ---------- */
  function renderWelcome() {
    var cfg = state.config || {};
    var wrap = el('div', 'w-welcome');
    wrap.appendChild(el('div', 'w-welcome__hi', '售后知识助手'));
    var id = state.engineer;
    var welcome = cfg.welcome || '我是售后知识助手，答案都带出处。';
    welcome = welcome.replace('{name}', id && id.identified ? id.name : '工程师');
    wrap.appendChild(el('p', null, escapeHtml(welcome)));

    var examples = cfg.examples || [];
    if (examples.length) {
      var chips = el('div', 'w-chips');
      examples.forEach(function (ex) {
        var b = el('button', 'w-chip');
        b.innerHTML = I.zap + '<span>' + escapeHtml(ex.q) + (ex.tag ? '<small>' + escapeHtml(ex.tag) + '</small>' : '') + '</span>';
        b.addEventListener('click', function () {
          if (!state.sending) sendQuestion(ex.q);
        });
        chips.appendChild(b);
      });
      wrap.appendChild(chips);
    }

    /* 示例之外还有"转人工"这条出路：让访客一眼看到系统不止会答题（转人工弹窗里带热线与值班时间） */
    if (!(cfg.features && cfg.features.handoff === false)) {
      var handoffRow = el('div', 'w-welcome__handoff');
      var handoffLink = el(
        'button',
        'w-handoff-link',
        I.headphone + '<span>没查到？联系值班工程师</span>'
      );
      handoffLink.title = cfg.handoffText || '转人工';
      handoffLink.addEventListener('click', function () { openHandoff(null); });
      handoffRow.appendChild(handoffLink);
      wrap.appendChild(handoffRow);
    }

    var empty = el('div', 'w-msg w-msg--ai');
    empty.appendChild(wrap);
    wBody.appendChild(empty);
  }

  /* ---------- 服务初始化：config + session ---------- */
  async function loadService(reuseConfig) {
    state.bootFail = false;
    state.gateBlocked = false;
    if (!reuseConfig || !state.config) {
      try {
        state.config = (await window.WidgetAPI.getConfig()) || {};
      } catch (e) {
        state.config = null;
        state.bootFail = true;
        renderBootFail();
        syncSendState();
        return false;
      }
      applyBrand(state.config.brandColor);
      applyFeatures();
      notifyBrand();
    }
    applyDemoState();
    try {
      var data = await window.WidgetAPI.createSession({
        identity: {
          source: state.identity.source || 'anonymous',
          claimedEngineerId: state.identity.claimedEngineerId || null
        },
        hostContext: state.hostContext || {},
        demoCode: DEMO_CODE || null
      });
      state.sessionId = data.sessionId;
      state.engineer = data.engineer || null;
      if (data.hostContext) state.hostContext = data.hostContext;
    } catch (e) {
      if (e && (e.code === 'DEMO_CODE_REQUIRED' || e.code === 'DEMO_CODE_INVALID'
        || e.code === 'DEMO_PAUSED')) {
        state.bootFail = false;
        state.gateBlocked = true;
        renderDemoGate(e);
        syncSendState();
        return false;
      }
      state.bootFail = true;
      renderBootFail();
      syncSendState();
      return false;
    }
    if (wBody.querySelector('.w-bootfail')) wBody.innerHTML = '';
    renderIdentity();
    renderCtxLine();
    if (!wBody.querySelector('.w-welcome') && !wBody.querySelector('.w-msg--user')) {
      renderWelcome();
    }
    syncSendState();
    return true;
  }

  async function ensureSession() {
    if (state.sessionId) return state.sessionId;
    var data = await window.WidgetAPI.createSession({
      identity: {
        source: state.identity.source || 'anonymous',
        claimedEngineerId: state.identity.claimedEngineerId || null
      },
      hostContext: state.hostContext || {},
      demoCode: DEMO_CODE || null
    });
    state.sessionId = data.sessionId;
    if (data.engineer) state.engineer = data.engineer;
    if (data.hostContext) state.hostContext = data.hostContext;
    renderIdentity();
    renderCtxLine();
    return state.sessionId;
  }

  /* ---------- 发送主流程 ---------- */
  function sendQuestion(text) {
    text = (text || wInput.value).trim();
    if (!text || state.sending || state.bootFail || state.offline) return;
    wInput.value = '';
    autoGrow();
    syncSendState();
    sendTurn(text);
  }

  async function sendTurn(text, retryNode, clarify) {
    state.sending = true;
    /* 每次真正发问都会在后端算一轮（拒答也算），所以先本地扣一次用于提示；
       追平以 /widget/config 的 sessionTurns 为准，后端 refused/quota 仍是裁判。 */
    if (typeof state.turnsLeft === 'number' && state.turnsLeft > 0) {
      state.turnsLeft -= 1;
      paintQuota();
    }
    autoFollow = true;
    syncSendState();
    if (!retryNode) {
      appendUserBubble(text);
      state.msgCount += 2;
    }
    var aiMsg = retryNode || beginAIMessage();
    if (retryNode) resetAILoading(retryNode);
    try {
      await runTurn(text, aiMsg, clarify);
    } finally {
      state.sending = false;
      syncSendState();
      scrollBottom();
    }
  }

  async function runTurn(text, aiMsg, clarify) {
    var sessionId;
    try {
      sessionId = await ensureSession();
    } catch (e) {
      renderFailure(aiMsg, text, { code: e.code, message: e.message || '会话建立失败，请重试', retryable: true });
      return;
    }

    state.citeOrder = [];
    state.citations = {};
    state.lastMessageId = null;
    var card = aiMsg.querySelector('.w-ai-card');
    var failed = false;
    var question = text;
    /* 追问选择：本次传入优先，其次沿用同一问题之前的确认（一轮为限，PRD FR-Q2.3） */
    var selection = clarify || state.clarified[text] || null;
    var ctrl = typeof AbortController === 'function' ? new AbortController() : null;
    state.abort = ctrl;

    function fail(error) {
      if (failed) return;
      failed = true;
      renderFailure(aiMsg, question, error || {});
    }

    try {
      await window.WidgetAPI.streamChat(
        {
          sessionId: sessionId,
          question: question,
          context: state.hostContext,
          clarify: selection,
          signal: ctrl ? ctrl.signal : undefined,
        },
        function (name, payload) {
          if (failed) return;
          if (name === 'structured') {
            renderStructured(card, payload.structured || {}, payload.filterNote);
          } else if (name === 'conflict') {
            renderConflict(card, payload || {});
          } else if (name === 'steps') {
            renderStep(card, payload || {});
          } else if (name === 'citations') {
            state.citations = buildCitationMap(payload.items);
            renderCitationBlock(card);
          } else if (name === 'ask') {
            renderAsk(aiMsg, payload.ask || payload, question);
          } else if (name === 'gap') {
            renderGap(aiMsg, payload.gap || {});
          } else if (name === 'safe') {
            renderSafe(aiMsg, payload.safe || {});
          } else if (name === 'refused') {
            renderQuotaRefused(aiMsg, payload.refused || {});
          } else if (name === 'done') {
            state.lastMessageId = payload.messageId || null;
            renderActionBar(card, payload);
          } else if (name === 'error') {
            fail(payload.error || {});
          }
          scrollBottom();
        }
      );
    } catch (e) {
      if (failed) return;
      var disconnected = (typeof navigator !== 'undefined' && navigator.onLine === false)
        || (e && e.name === 'AbortError');
      if (disconnected) {
        state.pendingRetry = { text: question, clarify: selection, node: aiMsg };
        renderFailure(aiMsg, question, {
          code: 'NETWORK',
          message: '与助手的连接中断了，本次回答未完成；网络恢复后会自动重试一次，也可以点「重新连接」立即重试。',
          retryable: true,
        });
      } else {
        fail({ code: e && e.code, message: (e && e.message) || '网络异常，本次未生成回答', retryable: true });
      }
    } finally {
      if (state.abort === ctrl) state.abort = null;
    }
  }

  function resetAILoading(aiMsg) {
    var card = aiMsg.querySelector('.w-ai-card');
    if (!card) return;
    card.innerHTML = '';
    card.appendChild(aiHead('正在重新检索手册与历史工单…'));
  }

  /* 失败兜底：不在原位留半成品，给出「重试 / 转人工」两条出路（spec §4.8A） */
  function renderFailure(aiMsg, text, error) {
    var card = aiMsg.querySelector('.w-ai-card');
    if (!card) return;
    card.innerHTML = '';
    card.appendChild(aiHead('售后知识助手'));

    var retryable = error.retryable !== false;
    var isNetwork = error.code === 'NETWORK';
    var title = isNetwork
      ? '连接中断，本次回答未完成'
      : (error.code === 'CHAT_FAILED' || !error.code ? '服务暂时不可用，请稍后重试' : '请求失败，请稍后重试');
    var body = error.message || '模型服务超时或报错，本次未生成回答；若持续失败请转人工，接通时会连同当前工单与对话一并带过去。';
    if (!retryable) body += '\n（该错误重试可能无效，建议直接转人工）';
    /* 重试 = 整包重问当前问题（D13 允许；不重复用户气泡）。
       契约里 retryable=false 只作提示，界面仍保留重试入口，避免用户无路可走。 */
    var retryLabel = isNetwork ? '重新连接' : '重试';
    var ops = '<button class="w-refuse__btn" data-act="retry">' + I.retry + retryLabel + '</button>' +
      '<button class="w-refuse__btn w-refuse__btn--ghost" data-act="handoff">' + I.headphone + '转人工</button>';

    var box = el('div', 'w-refuse w-refuse--gap');
    box.innerHTML =
      '<div>' + I.alert + '</div>' +
      '<div><div class="w-refuse__title">' + escapeHtml(title) + '</div>' +
      '<p>' + textHtml(body) + '</p>' +
      '<div class="w-refuse__ops">' + ops + '</div></div>';
    var retryBtn = box.querySelector('[data-act="retry"]');
    if (retryBtn) {
      retryBtn.addEventListener('click', function () {
        if (state.sending || state.bootFail) return;
        if (state.offline) {
          toast(I.alert + '网络尚未恢复，恢复后会自动重试本次提问');
          return;
        }
        state.pendingRetry = null;
        sendTurn(text, aiMsg);
      });
    }
    box.querySelector('[data-act="handoff"]').addEventListener('click', function () {
      openHandoff(state.lastMessageId);
    });
    card.appendChild(box);
    scrollBottom();
  }

  /* 服务不可达（连不上后端）：面板内给失败态与重试入口，宿主页面不受影响（spec §4.8B） */
  function renderBootFail() {
    wBody.innerHTML = '';
    var wrap = el('div', 'w-bootfail');
    var box = el('div', 'w-refuse w-refuse--gap');
    box.innerHTML =
      '<div>' + I.alert + '</div>' +
      '<div><div class="w-refuse__title">连接失败，暂时无法加载助手服务</div>' +
      '<p>助手服务地址不可达（网络不通或后端未启动）。宿主页面不受影响，请稍后重试；紧急情况可直接转人工。</p>' +
      '<div class="w-refuse__ops">' +
      '<button class="w-refuse__btn" data-act="retry">' + I.retry + '重试连接</button>' +
      '<button class="w-refuse__btn w-refuse__btn--ghost" data-act="handoff">' + I.headphone + '转人工</button>' +
      '</div></div>';
    box.querySelector('[data-act="retry"]').addEventListener('click', retryBoot);
    box.querySelector('[data-act="handoff"]').addEventListener('click', function () {
      openHandoff(null);
    });
    wrap.appendChild(box);
    wBody.appendChild(wrap);
    syncSendState();
  }

  async function retryBoot() {
    if (!state.bootFail) return;
    var ok = await loadService(true);
    if (ok) {
      wBody.innerHTML = '';
      renderWelcome();
      syncSendState();
      toast(I.check + '已重新连接助手服务');
    } else {
      toast(I.alert + '仍无法连接助手服务，请稍后重试');
    }
  }

  /* 演示会话剩余轮次：只显示「本次会话还可提问 N 次」。
     每日额度（码 / IP / 全局）不下发，也不显示——那是运营参数。
     前端计数只用于提示，真正的裁判仍是后端（refused/quota）。 */
  function applyDemoState() {
    var demo = (state.config && state.config.demo) || {};
    var limit = Number(demo.sessionTurns || 0);
    state.turnsLeft = (demo.enabled && limit > 0) ? limit : null;
    paintQuota();
  }

  function paintQuota() {
    if (!wQuota) return;
    if (typeof state.turnsLeft !== 'number') {
      wQuota.hidden = true;
      wQuota.textContent = '';
      return;
    }
    wQuota.hidden = false;
    wQuota.textContent = state.turnsLeft > 0
      ? '本次会话还可提问 ' + state.turnsLeft + ' 次'
      : '本次会话的提问次数已用完；重新打开助手会开始新的一次对话';
  }

  /* 演示入口码缺失/无效/演示暂停：这是"从哪里进入 / 还开不开放"的问题，不是服务故障，
     所以只给说明与预约入口。具体规则由后端返回，前端不猜。 */
  function renderDemoGate(error) {
    wBody.innerHTML = '';
    var wrap = el('div', 'w-bootfail');
    var box = el('div', 'w-refuse w-refuse--safe');
    var demo = (state.config && state.config.demo) || {};
    var booking = demo.bookingUrl || '';
    var paused = !!error && error.code === 'DEMO_PAUSED';
    var title = paused
      ? (demo.pausedTitle || '演示已暂停')
      : ((error && error.code === 'DEMO_CODE_INVALID')
        ? '入口码无效或已过期'
        : '这个演示需要从官网入口进入');
    var body = paused
      ? (demo.pausedText || (error && error.message) || '')
      : '请回到企业官网的案例页，点击「进入演示」打开——链接会自动带上入口码。'
        + '如果你是从别处拿到这个地址，可以预约一次 1:1 演示，我们按你的场景走一遍。';
    box.innerHTML =
      '<div>' + I.shield + '</div>' +
      '<div><div class="w-refuse__title">' + escapeHtml(title) + '</div>' +
      '<p>' + textHtml(body) + '</p>' +
      '<div class="w-refuse__ops">' +
      (booking
        ? '<a class="w-refuse__btn" href="' + escapeHtml(booking) + '" target="_blank" rel="noopener">预约演示</a>'
        : '') +
      '</div></div>';
    wrap.appendChild(box);
    wBody.appendChild(wrap);
    wCtxLine.textContent = paused ? '演示暂未开放' : '演示入口未验证';
    scrollBottom();
  }

  /* 演示额度用完（后端 refused/quota）：给"预约 1:1 演示"的出口，不展示具体阈值。 */
  function renderQuotaRefused(aiMsg, info) {
    var card = aiMsg.querySelector('.w-ai-card');
    if (!card) return;
    card.innerHTML = '';
    card.appendChild(aiHead('售后知识助手'));
    var box = el('div', 'w-refuse w-refuse--gap');
    var url = info.bookingUrl || ((state.config && state.config.demo) || {}).bookingUrl || '';
    box.innerHTML =
      '<div>' + I.shield + '</div>' +
      '<div><div class="w-refuse__title">' + escapeHtml(info.title || '本次演示额度已用完') + '</div>' +
      '<p>' + textHtml(info.body || '') + '</p>' +
      '<div class="w-refuse__ops">' +
      (url
        ? '<a class="w-refuse__btn" href="' + escapeHtml(url) + '" target="_blank" rel="noopener">预约 1:1 演示</a>'
        : '') +
      '</div></div>';
    card.appendChild(box);
    if (info.kind === 'paused') {
      state.turnsLeft = null;   // 暂停不是"用完"，不再显示剩余次数
    } else if (info.reason === 'session') {
      state.turnsLeft = 0;
    }
    paintQuota();
    scrollBottom();
  }

  /* ---------- 断网提示（真实 navigator offline/online；不做自动重发） ---------- */
  function renderOffline() {
    wNetbar.hidden = !state.offline;
    if (state.offline) wNetText.textContent = '网络已断开；恢复后会自动重试本次提问（不续传半截回答）。';
    syncSendState();
  }

  /* ---------- 消息外壳 ---------- */
  function aiHead(text) {
    return el('div', 'w-ai-head', '<span class="w-avatar">' + I.wrench + '</span><span>' + escapeHtml(text) + '</span>');
  }
  function appendUserBubble(text) {
    var m = el('div', 'w-msg w-msg--user');
    m.appendChild(el('div', 'w-bubble', escapeHtml(text)));
    wBody.appendChild(m);
    scrollBottom();
  }
  function beginAIMessage() {
    var m = el('div', 'w-msg w-msg--ai');
    var card = el('div', 'w-ai-card');
    card.appendChild(aiHead('正在检索手册与历史工单…'));
    m.appendChild(card);
    wBody.appendChild(m);
    scrollBottom();
    return m;
  }

  /* ---------- 结构化理解卡 ---------- */
  function renderStructured(card, structured, filterNote) {
    var s = structured || {};
    function kv(label, value) {
      return value
        ? '<span class="w-kv">' + label + ' <b>' + escapeHtml(value) + '</b></span>'
        : '<span class="w-kv w-kv--empty">' + label + ' 未提供 · 全库检索</span>';
    }
    var box = el('div', 'w-struct');
    box.innerHTML =
      '<div class="w-struct__title">' + I.filter + '结构化检索条件（由现象描述整理，未提供的字段不猜测）</div>' +
      '<div class="w-struct__fields">' +
      kv('现象', s.symptom) + kv('型号', s.model) + kv('批次', s.batch) + kv('报警码', s.code) +
      '</div>' +
      (filterNote ? '<div class="w-struct__filter">' + I.check + escapeHtml(filterNote) + '</div>' : '');
    card.appendChild(box);
    return box;
  }

  /* ---------- 多版本冲突提示（纪律 7：并列展示，不替工程师选择） ---------- */
  function renderConflict(card, payload) {
    var options = payload.options || [];
    var optHtml = options.length
      ? '<div class="w-conflict__opts">' + options.map(function (opt) {
          var label = escapeHtml(opt.label || '');
          var count = (opt.evidenceIds || []).length;
          return '· ' + label + (count ? '（' + count + ' 条依据）' : '');
        }).join('<br>') + '</div>'
      : '';
    var box = el('div', 'w-conflict');
    box.innerHTML =
      '<div>' + I.alert + '</div>' +
      '<div><b>检索到多版本冲突，系统不替你选择</b><br>' +
      escapeHtml(payload.conflict || '') + optHtml + '</div>';
    card.appendChild(box);
    return box;
  }

  /* ---------- 步骤清单（打字机 + 引用角标，spec §4.3③） ---------- */
  function stepVisibleText(text, citations) {
    var ids = citations || [];
    var cleaned = String(text || '');
    if (ids.length) {
      var pattern = new RegExp('[\\[【]\\s*(' + ids.map(escapeRegExp).join('|') + ')\\s*[\\]】]', 'g');
      cleaned = cleaned.replace(pattern, '');
      /* 模型有时把证据 ID 直接写进正文（不带括号）：M29023规定…，这类原始标记同样隐藏，
         统一由行尾编号角标承担引用关系（否则业务读者会看到满屏 M 编号）。 */
      var bare = new RegExp(
        '(^|[^A-Za-z0-9])(' + ids.map(escapeRegExp).join('|') + ')(?=[^A-Za-z0-9]|$)',
        'g'
      );
      cleaned = cleaned.replace(bare, '$1');
    }
    return cleaned
      /* 生成截断可能留下半个引用标记（如行尾的 "[M"），连同空白一起清掉 */
      .replace(/[\s]*[\[【]\s*M[0-9]*\s*$/u, '')
      .replace(/[ \t]{2,}/g, ' ')
      .replace(/[ \t]+([，。；、！？])/g, '$1')
      .trim();
  }

  function renderStep(card, payload) {
    if (!card.querySelector('.w-steps-label')) {
      card.appendChild(el('div', 'w-cites__label w-steps-label', I.check + '排查步骤（按顺序执行）'));
    }
    var ol = card.querySelector('.w-steps');
    if (!ol) {
      ol = el('ol', 'w-steps');
      card.appendChild(ol);
    }
    var li = el('li');
    li.appendChild(el('span', 'w-steps__no', String(payload.index)));
    var txt = el('span', 'w-steps__txt');
    li.appendChild(txt);
    ol.appendChild(li);

    var ids = (payload.citations || []).slice();
    ids.forEach(function (id) {
      if (state.citeOrder.indexOf(id) < 0) state.citeOrder.push(id);
    });
    var visible = stepVisibleText(payload.text, ids);
    typewriter(txt, visible).then(function () { appendCiteRefs(txt, ids); });
    scrollBottom();
  }

  function appendCiteRefs(node, ids) {
    ids.forEach(function (id) {
      var index = state.citeOrder.indexOf(id);
      if (index < 0) return;
      var sup = el('sup', 'w-cite-ref', String(index + 1));
      sup.dataset.cite = id;
      sup.title = '核对这条步骤的依据';
      sup.addEventListener('click', function () { focusCitation(id); });
      node.appendChild(sup);
    });
  }

  function typewriter(node, text) {
    if (reduceMotion() || !text) {
      node.textContent = text || '';
      return Promise.resolve();
    }
    activeTypers += 1;
    return new Promise(function (resolve) {
      var i = 0;
      /* 真实答案单步可能很长（100–300 字）：按长度自适应步长，整步约 0.6–0.9s 呈现完 */
      var step = Math.max(2, Math.ceil(text.length / 45));
      var timer = setInterval(function () {
        i += step;
        node.textContent = text.slice(0, i);
        if (autoFollow) scrollBottom();
        if (i >= text.length) {
          clearInterval(timer);
          activeTypers -= 1;
          resolve();
        }
      }, 16);
    });
  }

  /* 点步骤角标 → 定位并展开对应引用卡，短暂高亮（spec §4.3④） */
  function focusCitation(id) {
    var target = wBody.querySelector('.w-cite[data-cite="' + id + '"]');
    if (!target) return;
    target.classList.add('is-open', 'is-flash');
    loadEvidence(target, id);
    target.scrollIntoView({ block: 'nearest', behavior: reduceMotion() ? 'auto' : 'smooth' });
    setTimeout(function () { target.classList.remove('is-flash'); }, 1700);
  }

  /* ---------- 引用卡（点击展开原文；原文来自 GET /widget/evidence/{id}） ---------- */
  function buildCitationMap(items) {
    var map = {};
    (items || []).forEach(function (item) {
      if (!item || !item.evidenceId) return;
      map[item.evidenceId] = item;
      if (state.citeOrder.indexOf(item.evidenceId) < 0) state.citeOrder.push(item.evidenceId);
    });
    return map;
  }

  function renderCitationBlock(card) {
    if (card.querySelector('.w-cites--all') || !state.citeOrder.length) return;
    var entries = state.citeOrder.map(function (id, i) { return { id: id, no: i + 1 }; });
    var box = renderCitations(entries, { numbered: true, label: true });
    box.classList.add('w-cites--all');
    card.appendChild(box);
  }

  function renderCitations(entries, opts) {
    var o = opts || {};
    var wrap = el('div', 'w-cites');
    if (o.label) {
      wrap.appendChild(el('div', 'w-cites__label', I.file + '引用来源（点击核对原文）'));
    }
    entries.forEach(function (entry) {
      var id = entry.id;
      var item = state.citations[id] || { evidenceId: id, kind: 'manual', title: id, loc: '' };
      var kindClass = item.kind === 'ticket' ? ' w-cite--ticket' : '';
      var icon = item.kind === 'ticket' ? I.ticket : I.file;
      var title = escapeHtml(item.title || id);
      /* 后端 citation.title 已带章节（如《维修手册》§4.4.1），loc 又是 "§4.4.1 · P10–11"；
         去掉重复章节，只保留页码段，避免 "§4.4.1 §4.4.1 · P10–11"。 */
      var locText = item.loc || '';
      var titleText = item.title || id;
      var section = locText.split('·')[0].trim();
      if (section && titleText.indexOf(section) >= 0 && locText.indexOf('·') >= 0) {
        locText = '· ' + locText.slice(locText.indexOf('·') + 1).trim();
      }
      var meta = locText ? '<span class="w-cite__meta"> ' + escapeHtml(locText) + '</span>' : '';
      var batch = item.batch ? '<span class="w-cite__batch">' + escapeHtml(item.batch) + ' 批次适用</span>' : '';

      var c = el('div', 'w-cite' + kindClass);
      c.dataset.cite = id;
      c.innerHTML =
        '<div class="w-cite__head">' +
        '<span class="w-cite__lead">' +
        (o.numbered && entry.no ? '<span class="w-cite__no">' + entry.no + '</span>' : '') + icon +
        '</span>' +
        '<span class="w-cite__title">' + title + meta + '</span>' +
        '<span class="w-cite__chev">' + I.chev + '</span>' +
        batch +
        '</div>' +
        '<div class="w-cite__orig"></div>';
      c.addEventListener('click', function () {
        var open = c.classList.toggle('is-open');
        if (open) loadEvidence(c, id);
      });
      wrap.appendChild(c);
      if (o.store !== false) state.citations[id] = item;
    });
    return wrap;
  }

  async function loadEvidence(card, id) {
    var box = card.querySelector('.w-cite__orig');
    if (!box || box.dataset.loaded === '1' || box.dataset.loading === '1') return;
    if (state.config && state.config.features && state.config.features.evidence === false) {
      box.textContent = '本次部署未开启引用原文';
      box.dataset.loaded = '1';
      return;
    }
    if (state.evidenceCache[id]) {
      box.textContent = state.evidenceCache[id].text || '（原文为空）';
      box.dataset.loaded = '1';
      return;
    }
    box.dataset.loading = '1';
    box.classList.add('is-loading');
    box.textContent = '加载原文…';
    try {
      var sessionId = await ensureSession();
      var evidence = await window.WidgetAPI.getEvidence(sessionId, id);
      state.evidenceCache[id] = evidence;
      box.textContent = evidence.text || '（原文为空）';
      box.dataset.loaded = '1';
      /* 后端原文里可能有更完整的标题/出处，回填卡片（失败不影响已展示内容） */
      var titleNode = card.querySelector('.w-cite__title');
      if (titleNode && evidence.title) {
        var meta = evidence.loc ? '<span class="w-cite__meta"> ' + escapeHtml(evidence.loc) + '</span>' : '';
        titleNode.innerHTML = escapeHtml(evidence.title) + meta;
      }
    } catch (e) {
      box.textContent = (e && e.message) || '原文加载失败，请稍后重试';
    } finally {
      box.dataset.loading = '0';
      box.classList.remove('is-loading');
    }
  }

  /* ---------- 操作条：已解决 / 没解决 / 收藏（真实 POST /widget/feedback） ---------- */
  async function pushFeedback(messageId, type) {
    var sessionId = await ensureSession();
    await window.WidgetAPI.sendFeedback(sessionId, messageId, type);
  }

  function renderActionBar(card, donePayload) {
    if (card.querySelector('.w-actions')) return;
    if (state.config && state.config.features && state.config.features.feedback === false) return;
    var messageId = (donePayload && donePayload.messageId) || state.lastMessageId;
    if (!messageId) return;

    var bar = el('div', 'w-actions');
    var ok = el('button', 'w-act w-act--ok', I.checkCircle + '<span>已解决</span>');
    var no = el('button', 'w-act w-act--no', I.xCircle + '<span>没解决</span>');
    var save = el('button', 'w-act w-act--save', I.bookmark + '<span>收藏入库</span>');

    ok.addEventListener('click', async function () {
      if (ok.classList.contains('is-active') || state.sending) return;
      ok.classList.add('is-active');
      no.classList.remove('is-active');
      try {
        await pushFeedback(messageId, 'resolved');
        var id = state.engineer;
        toast(I.check + '已标记「已解决」，本次定位用时已记录'
          + (id && id.identified ? '' : '（未识别身份，不计入按人指标）'));
      } catch (e) {
        ok.classList.remove('is-active');
        toast(I.alert + '反馈提交失败：' + escapeHtml((e && e.message) || '请稍后重试'));
      }
    });

    no.addEventListener('click', async function () {
      if (no.classList.contains('is-active') || state.sending) return;
      no.classList.add('is-active');
      ok.classList.remove('is-active');
      try {
        await pushFeedback(messageId, 'unresolved');
        toast(I.xCircle + '已记为坏例并进入后台复盘，感谢反馈');
      } catch (e) {
        no.classList.remove('is-active');
        toast(I.alert + '反馈提交失败：' + escapeHtml((e && e.message) || '请稍后重试'));
      }
    });

    save.addEventListener('click', async function () {
      if (save.classList.contains('is-active') || state.sending) return;
      save.classList.add('is-active');
      try {
        await pushFeedback(messageId, 'saved');
        var id = state.engineer;
        toast(I.bookmark + (id && id.identified
          ? '已记录收藏意向，按规则需主管确认后才入库'
          : '已记录收藏意向（作者「未识别」），主管审核后仍可入库'));
      } catch (e) {
        save.classList.remove('is-active');
        toast(I.alert + '收藏失败：' + escapeHtml((e && e.message) || '请稍后重试'));
      }
    });

    bar.appendChild(ok);
    bar.appendChild(no);
    bar.appendChild(save);
    bar.appendChild(el('span', 'w-act-note', '你的反馈决定知识库怎么长大'));
    card.appendChild(bar);
    scrollBottom();
  }

  /* ---------- 单轮追问卡（D-005：后端暂不产生 ask 事件；结构按契约保留） ---------- */
  function renderAsk(aiMsg, ask, question) {
    var card = aiMsg.querySelector('.w-ai-card');
    if (!card) return;
    card.innerHTML = '';
    card.appendChild(aiHead('售后知识助手 · 需要你确认一个条件'));
    var box = el('div', 'w-ask');
    var options = ask.options || [];
    box.innerHTML =
      '<div class="w-ask__title">' + I.filter + escapeHtml(ask.title || '先确认一个条件') + '</div>' +
      '<p class="w-ask__body">' + textHtml(ask.body || '') + '</p>' +
      '<div class="w-ask__opts">' + options.map(function (opt) {
        return '<button class="w-ask__opt" data-value="' + escapeHtml(opt.value) + '">' +
          '<b>' + escapeHtml(opt.label || opt.value) + '</b>' +
          '<small>' + escapeHtml(opt.hint || '') + '</small></button>';
      }).join('') + '</div>' +
      '<div class="w-ask__hint">只追问这一次；选定后直接给出对应方案，不再打断。</div>';
    box.querySelectorAll('.w-ask__opt').forEach(function (btn) {
      btn.addEventListener('click', function () {
        if (state.sending) return;
        var opt = options.filter(function (o) { return String(o.value) === btn.dataset.value; })[0];
        if (opt) pickClarify(aiMsg, question, ask, opt);
      });
    });
    card.appendChild(box);
    scrollBottom();
  }

  async function pickClarify(aiMsg, question, ask, opt) {
    aiMsg.querySelectorAll('.w-ask__opt').forEach(function (btn) {
      btn.disabled = true;
      btn.classList.toggle('is-picked', btn.dataset.value === String(opt.value));
    });
    var selection = { field: ask.field, value: opt.value, label: opt.label || opt.value };
    state.clarified[question] = selection;  // 记住：同一问题不再追问第二次
    appendUserBubble(opt.label || opt.value);
    state.msgCount += 2;
    var next = beginAIMessage();
    await sendTurn(question, next, selection);
  }

  /* ---------- 拒答卡（证据不足必须拒答并转人工，PRD §5.2） ---------- */
  function renderGap(aiMsg, gap) {
    var card = aiMsg.querySelector('.w-ai-card');
    if (!card) return;
    card.innerHTML = '';
    card.appendChild(aiHead('售后知识助手'));
    if (gap.messageId) state.lastMessageId = gap.messageId;

    var box = el('div', 'w-refuse w-refuse--gap');
    box.innerHTML =
      '<div>' + I.xCircle + '</div>' +
      '<div><div class="w-refuse__title">' + escapeHtml(gap.title || '未查到可靠依据，本次不作答') + '</div>' +
      '<p>' + textHtml(gap.body || '') + '</p>' +
      '<button class="w-refuse__btn" data-act="handoff">' + I.headphone + '转人工处理</button></div>';
    box.querySelector('[data-act="handoff"]').addEventListener('click', function () {
      openHandoff(gap.messageId || state.lastMessageId);
    });
    card.appendChild(box);
    scrollBottom();
  }

  /* ---------- 安全卡（不生成任何操作步骤，只提示联系厂家并引用安规） ---------- */
  function renderSafe(aiMsg, safe) {
    var card = aiMsg.querySelector('.w-ai-card');
    if (!card) return;
    card.innerHTML = '';
    card.appendChild(aiHead('安全拦截'));
    if (safe.messageId) state.lastMessageId = safe.messageId;

    var box = el('div', 'w-refuse w-refuse--safe');
    box.innerHTML =
      '<div>' + I.shield + '</div>' +
      '<div><div class="w-refuse__title">' + escapeHtml(safe.title || '涉及高风险作业，不提供操作步骤') + '</div>' +
      '<p>' + textHtml(safe.body || '') + '</p></div>';

    var citeWrap = el('div');
    var items = safe.citations || [];
    if (items.length) {
      state.citeOrder = [];
      buildCitationMap(items);
      citeWrap.appendChild(renderCitations(items.map(function (item) { return { id: item.evidenceId }; }), { compact: true, store: false }));
    }
    var btn = el('button', 'w-refuse__btn');
    btn.innerHTML = I.headphone + '联系厂家 / 持证人员';
    btn.addEventListener('click', function () { openHandoff(safe.messageId || state.lastMessageId); });
    citeWrap.appendChild(btn);
    box.children[1].appendChild(citeWrap);
    card.appendChild(box);
    scrollBottom();
  }

  /* ---------- 转人工（真实 POST /widget/handoff；字段全部来自后端快照） ---------- */
  function renderCarry(data) {
    var ctx = data.context || {};
    var id = state.engineer;
    var idLine = id && id.identified
      ? id.name + '（工号 ' + id.account + '）· 来源：' + (id.sourceLabel || id.source)
      : (id && id.claimed ? '工号 ' + id.claimed + ' 未开通' : '未识别身份')
        + '（' + (id ? (id.sourceLabel || id.source) : '匿名') + '）· 不计入个人统计';
    var rounds = ctx.rounds || 0;
    var cited = ctx.citedCount || 0;
    var handoffs = ctx.handoffCount || 0;
    var rows = [
      ['当前工单', data.ticketNo || '宿主未提供（无上下文页面）'],
      ['设备 / 批次', (ctx.deviceModel || '—') + ' / ' + (ctx.batch ? ctx.batch + ' 批次' : '—')],
      ['身份', idLine],
      ['对话记录', rounds ? rounds + ' 轮' : '暂无'],
      ['已引用依据', cited ? cited + ' 条（含手册章节/工单编号）' : '暂无'],
      ['转人工次数', handoffs ? handoffs + ' 次（含本次）' : '1 次（本次）']
    ];
    wCarryList.innerHTML = rows.map(function (row) {
      return '<li><span>' + escapeHtml(row[0]) + '</span><b>' + escapeHtml(row[1]) + '</b></li>';
    }).join('');
  }

  async function openHandoff(messageId) {
    var sessionId;
    try {
      sessionId = await ensureSession();
    } catch (e) {
      toast(I.alert + '会话未建立，暂时无法转人工');
      return;
    }
    try {
      var data = await window.WidgetAPI.handoff(sessionId, messageId || null);
      renderCarry(data);
      wHotline.textContent = data.hotline || '—';
      wHotlineMeta.textContent = data.hours ? '值班时间 ' + data.hours : '值班时间未提供';
      wHotlineNote.textContent = data.text || (state.config && state.config.handoffText) || '';
      modal.hidden = false;
      if (window.parent !== window) {
        window.parent.postMessage({ type: 'moqian:handoff', payload: data }, '*');
      }
    } catch (e) {
      toast(I.alert + '转人工失败：' + escapeHtml((e && e.message) || '请稍后重试'));
    }
  }

  wHandoffHead.addEventListener('click', function () { openHandoff(state.lastMessageId); });
  $('wHandoffCancel').addEventListener('click', function () { modal.hidden = true; });
  $('wHandoffOk').addEventListener('click', function () {
    modal.hidden = true;
    toast(I.headphone + '已为你接通值班工程师通道');
  });
  modal.addEventListener('click', function (e) { if (e.target === modal) modal.hidden = true; });

  /* ---------- 输入区 ---------- */
  function autoGrow() {
    wInput.style.height = 'auto';
    wInput.style.height = Math.min(wInput.scrollHeight, 88) + 'px';
  }
  wInput.addEventListener('input', function () { autoGrow(); syncSendState(); });
  wInput.addEventListener('keydown', function (e) {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      sendQuestion();
    }
  });
  wSend.addEventListener('click', function () { sendQuestion(); });
  wClose.addEventListener('click', function () {
    if (window.parent !== window) window.parent.postMessage({ type: 'moqian:close' }, '*');
  });

  /* ---------- 宿主通信 ---------- */
  var bootStarted = false;
  var bootTimer = null;

  function applyContextMessage(context, identity) {
    state.hostContext = context || {};
    state.identity = identity || { source: 'host_injected', claimedEngineerId: null };
    state.sessionId = null;
    state.engineer = null;
    state.gateBlocked = false;
    state.lastMessageId = null;
    state.msgCount = 0;
    state.citeOrder = [];
    state.citations = {};
    state.evidenceCache = {};
    state.clarified = {};
    wBody.innerHTML = '';
    renderIdentity();
    renderCtxLine();
    syncSendState();
  }

  window.addEventListener('message', function (event) {
    var data = event.data || {};
    if (data.type === 'moqian:context') {
      applyContextMessage(data.context, data.identity);
      if (!bootStarted) {
        if (bootTimer) clearTimeout(bootTimer);
        bootTimer = null;
        bootStarted = true;
        loadService(false);
      } else {
        loadService(true);
      }
    } else if (data.type === 'moqian:open') {
      wInput.focus();
    }
  });

  window.addEventListener('offline', function () {
    state.offline = true;
    renderOffline();
    if (state.sending && state.abort) {
      try { state.abort.abort(); } catch (e) { /* 已在错误分支处理 */ }
    }
  });
  window.addEventListener('online', function () {
    var wasOffline = state.offline;
    state.offline = false;
    renderOffline();
    if (!wasOffline) return;
    var pending = state.pendingRetry;
    state.pendingRetry = null;
    if (pending && !state.sending) {
      toast(I.check + '网络已恢复，正在自动重试本次提问');
      sendTurn(pending.text, pending.node, pending.clarify);
    } else {
      toast(I.check + '网络已恢复，可以继续提问');
    }
  });

  /* ---------- 启动 ---------- */
  function readBootContext() {
    /* 独立打开（无宿主）时可用 URL 参数模拟宿主上下文，便于演示与联调：
       widget.html?deviceModel=HY-G1000/1.6-JXW&ticketNo=HY-WO-2026-0107&engineerId=HY-ENG-006 */
    var ctx = {};
    ['deviceModel', 'deviceName', 'batch', 'ticketNo'].forEach(function (key) {
      var value = params.get(key);
      if (value) ctx[key] = value;
    });
    if (Object.keys(ctx).length) state.hostContext = ctx;

    var engineerId = params.get('engineerId');
    if (engineerId) {
      state.identity = {
        source: params.get('source') || 'host_injected',
        claimedEngineerId: engineerId
      };
    } else if (params.get('source')) {
      state.identity = { source: params.get('source'), claimedEngineerId: null };
    }
  }

  function boot() {
    readBootContext();
    state.offline = !navigator.onLine;
    renderIdentity();
    renderCtxLine();
    renderOffline();
    syncSendState();

    /* 先告诉宿主 iframe 已就绪：loader 会立刻 flush 暂存的上下文；
       给这条 postMessage 留一个消息回合再自行启动，避免先建一个无上下文的会话。 */
    if (window.parent !== window) {
      window.parent.postMessage({ type: 'moqian:ready' }, '*');
    }
    bootTimer = setTimeout(function () {
      bootTimer = null;
      if (bootStarted) return;
      bootStarted = true;
      loadService(false);
    }, 200);
  }

  boot();
})();
