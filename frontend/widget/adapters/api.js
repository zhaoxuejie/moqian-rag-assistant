/* ============================================================
 * api.js — 真实接口适配层（替代早期原型的 mock 实现）
 *
 * 只做三件事：拼 URL / 带 X-Widget-Session / 解析 SSE。
 * 业务状态机在 app.js；接口形状以 contracts/openapi.yaml 为准：
 *   GET  /widget/config
 *   POST /widget/session
 *   POST /chat                      （SSE，必须带 X-Widget-Session）
 *   GET  /widget/evidence/{id}      （必须带 X-Widget-Session）
 *   POST /widget/feedback
 *   POST /widget/handoff
 * ============================================================ */
window.WidgetAPI = (function () {
  'use strict';

  /* data-server 留空 = 与宿主同源（app 托管在 /widget-app）；独立调试时可用 ?server=http://127.0.0.1:8000 */
  var HOST = '';

  function setServer(server) {
    HOST = (server || '').replace(/\/$/, '');
  }

  function url(path) {
    return HOST + path;
  }

  function parseError(status, body) {
    var code = 'HTTP_' + status;
    var message = '请求失败（' + status + '）';
    if (body && typeof body === 'object') {
      if (body.code) code = body.code;
      if (body.message) message = body.message;
    }
    var err = new Error(message);
    err.code = code;
    err.status = status;
    return err;
  }

  async function readJson(resp) {
    try {
      return await resp.json();
    } catch (e) {
      return null;
    }
  }

  async function jsonRequest(path, options) {
    var resp = await fetch(url(path), options);
    var body = await readJson(resp);
    if (!resp.ok) throw parseError(resp.status, body);
    return body;
  }

  function sessionHeaders(sessionId) {
    return {
      'Content-Type': 'application/json',
      'X-Widget-Session': sessionId,
    };
  }

  /* ---------- SSE 帧解析 ----------
   * 服务端帧格式固定为 `event: <name>\ndata: <json>\n\n`（app/sse.py），
   * 这里按空行切帧，容忍 CRLF 与多行 data。 */
  function parseFrame(raw) {
    var name = 'message';
    var dataLines = [];
    raw.split(/\r?\n/).forEach(function (line) {
      if (line.indexOf('event:') === 0) {
        name = line.slice(6).trim();
      } else if (line.indexOf('data:') === 0) {
        dataLines.push(line.slice(5).replace(/^ /, ''));
      }
    });
    if (!dataLines.length) return null;
    try {
      return { name: name, data: JSON.parse(dataLines.join('\n')) };
    } catch (e) {
      return null;
    }
  }

  /**
   * POST /chat 并逐帧回调。
   * @param {{sessionId:string, question:string, context:object, clarify:object|null}} payload
   * @param {(name:string, data:object)=>void} onEvent
   */
  async function streamChat(payload, onEvent) {
    var resp = await fetch(url('/chat'), {
      method: 'POST',
      headers: sessionHeaders(payload.sessionId),
      signal: payload.signal || undefined,
      body: JSON.stringify({
        sessionId: payload.sessionId,
        question: payload.question,
        context: payload.context || undefined,
        clarify: payload.clarify || undefined,
      }),
    });
    if (!resp.ok) {
      throw parseError(resp.status, await readJson(resp));
    }
    if (!resp.body || typeof resp.body.getReader !== 'function') {
      var err = new Error('当前浏览器不支持流式读取');
      err.code = 'NO_STREAM';
      throw err;
    }

    var reader = resp.body.getReader();
    var decoder = new TextDecoder('utf-8');
    var buffer = '';
    try {
      while (true) {
        var chunk = await reader.read();
        if (chunk.done) break;
        buffer += decoder.decode(chunk.value, { stream: true });
        var idx;
        while ((idx = buffer.indexOf('\n\n')) >= 0) {
          var raw = buffer.slice(0, idx);
          buffer = buffer.slice(idx + 2);
          var frame = parseFrame(raw);
          if (frame) onEvent(frame.name, frame.data);
        }
      }
      if (buffer.trim()) {
        var tail = parseFrame(buffer);
        if (tail) onEvent(tail.name, tail.data);
      }
    } finally {
      try { reader.releaseLock(); } catch (e) { /* ignore */ }
    }
  }

  return {
    setServer: setServer,

    getConfig: function () {
      return jsonRequest('/widget/config', { method: 'GET' });
    },

    /**
     * POST /widget/session
     * @param {{identity:{source:string, claimedEngineerId:string|null}, hostContext:object}} payload
     */
    createSession: function (payload) {
      return jsonRequest('/widget/session', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
    },

    /** GET /widget/evidence/{id} —— 只返回本会话引用过的证据（后端保证） */
    getEvidence: function (sessionId, evidenceId) {
      return jsonRequest('/widget/evidence/' + encodeURIComponent(evidenceId), {
        method: 'GET',
        headers: { 'X-Widget-Session': sessionId },
      });
    },

    /** POST /widget/feedback —— type: resolved | unresolved | saved */
    sendFeedback: function (sessionId, messageId, type) {
      return jsonRequest('/widget/feedback', {
        method: 'POST',
        headers: sessionHeaders(sessionId),
        body: JSON.stringify({ sessionId: sessionId, messageId: messageId, type: type }),
      });
    },

    /** POST /widget/handoff —— 返回 hotline / hours / ticketNo / context 快照 */
    handoff: function (sessionId, messageId) {
      return jsonRequest('/widget/handoff', {
        method: 'POST',
        headers: sessionHeaders(sessionId),
        body: JSON.stringify({ sessionId: sessionId, messageId: messageId || null }),
      });
    },

    streamChat: streamChat,
  };
})();
