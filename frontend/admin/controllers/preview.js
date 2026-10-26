/* ============================================================
 * preview.js — 管理员知识自测浮层（PRD FR-P3 / admin spec §4.13）
 *
 * 真实接口：POST /admin/preview/ask（只检索 + 证据门，不生成答案、不写日志）。
 * 隔离：不创建 session、不写 messages、不进入问答日志与任何指标；刷新即清。
 * ============================================================ */
(function () {
  "use strict";

  const page = document.body && document.body.dataset.page;
  if (!page || page === "login") return;

  /* 抽测是管理员工具：普通用户不渲染入口（admin spec §4.13） */
  try {
    const me = JSON.parse(localStorage.getItem("admin_user") || "null");
    if (me && me.role && me.role !== "admin") return;
  } catch (e) { /* 解析失败按未登录处理，交给页面鉴权 */ }

  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g, (ch) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[ch]
  ));

  const root = document.createElement("div");
  root.className = "pv-root";
  root.innerHTML =
    '<button class="pv-launcher" id="pvLauncher" type="button" title="知识自测（管理员）">'
    + '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
    + '<path d="M10 2v7.31"/><path d="M14 9.3V1.99"/><path d="M8.5 2h7"/>'
    + '<path d="M14 9.3a6.5 6.5 0 1 1-4 0"/><path d="M5.52 16h12.96"/></svg>'
    + "</button>"
    + '<section class="pv-panel" id="pvPanel" hidden>'
    + '<header class="pv-head">'
    + '<div class="pv-head__title"><svg viewBox="0 0 24 24"><path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/></svg>'
    + "知识自测 <span class=\"pv-tag\">抽测 · 不计入指标</span></div>"
    + '<div class="pv-head__ops"><button class="pv-iconbtn" id="pvClear" type="button" title="清空抽测记录">清空</button>'
    + '<button class="pv-iconbtn" id="pvClose" type="button" title="收起"><svg viewBox="0 0 24 24"><path d="M18 6 6 18M6 6l12 12"/></svg></button></div>'
    + "</header>"
    + '<div class="pv-ctx">'
    + '<label>型号<input id="pvModel" type="text" placeholder="如 HY-G1000/1.6-JXW（可留空）" /></label>'
    + '<label>批次<input id="pvBatch" type="text" placeholder="批次（可留空）" /></label>'
    + "</div>"
    + '<div class="pv-body" id="pvBody">'
    + '<div class="pv-welcome">'
    + "<p>治理动作之后，先在这里问一句，确认知识库到底命中了什么。</p>"
    + '<div class="pv-welcome__tips">'
    + "<div>· 未填的型号/批次按全库检索，<b>系统不猜</b></div>"
    + "<div>· 结果给出「命中证据」与相关度，可点开对照切片</div>"
    + "<div>· 抽测<b>不计入任何指标、不写问答日志</b>，刷新即清</div>"
    + "</div>"
    + '<div class="pv-samples">'
    + '<button class="pv-sample" data-q="HY-E006 抱闸故障，运行中有周期性异响，怎么处理？" type="button">HY-E006 + 异响</button>'
    + '<button class="pv-sample" data-q="平层误差偏大怎么调整？" type="button">平层误差</button>'
    + '<button class="pv-sample" data-q="门机控制器报 E30 怎么处理？" type="button">门机 E30（看缺口）</button>'
    + "</div></div>"
    + "</div>"
    + '<footer class="pv-foot">'
    + '<div class="pv-input"><input id="pvInput" type="text" placeholder="描述故障现象，回车发送…" /><button class="pv-send" id="pvSend" type="button" title="发送">'
    + '<svg viewBox="0 0 24 24"><path d="m22 2-7 20-4-9-9-4Z"/><path d="M22 2 11 13"/></svg></button></div>'
    + '<div class="pv-foot__note">POST /admin/preview/ask · 只检索不回答案 · 不进入问答日志与任何指标'
    + ' · <a href="eval.html">要系统性验证请去评测面板</a></div>'
    + "</footer></section>";
  document.body.appendChild(root);

  const $ = (id) => root.querySelector("#" + id);
  const launcher = $("pvLauncher"), panel = $("pvPanel"), body = $("pvBody");
  const input = $("pvInput"), sendBtn = $("pvSend"), modelEl = $("pvModel"), batchEl = $("pvBatch");
  const history = [];
  let busy = false;

  function scrollBottom() { body.scrollTop = body.scrollHeight; }

  function chipsHtml(s) {
    const kv = (label, val) => val
      ? '<span class="pv-chip"><i>' + label + "</i>" + esc(val) + "</span>"
      : '<span class="pv-chip pv-chip--empty"><i>' + label + "</i>未提供 · 全库检索</span>";
    return kv("现象", s.symptom) + kv("型号", s.model) + kv("批次", s.batch) + kv("报警码", s.alarm);
  }

  function evidenceHtml(hits) {
    if (!hits || !hits.length) return "";
    return '<div class="pv-ev">' + hits.map((h) => {
      const score = h.score == null ? "" : Number(h.score).toFixed(2);
      const link = h.docId
        ? '<a class="pv-ev__link" href="document.html?id=' + encodeURIComponent(h.docId) + '">查看切片</a>'
        : "";
      return '<div class="pv-ev__row">'
        + '<div class="pv-ev__id">' + esc(h.evidenceId) + "</div>"
        + '<div class="pv-ev__body"><div class="pv-ev__loc">' + esc(h.loc || h.title || "")
        + '<span class="pv-ev__page">' + esc(h.batch ? " · " + h.batch + " 批次" : "") + "</span></div>"
        + '<div class="pv-ev__excerpt">' + esc(String(h.text || "").replace(/\n/g, " ").slice(0, 88)) + "…</div></div>"
        + '<div class="pv-ev__right"><b>' + score + "</b>" + link + "</div>"
        + "</div>";
    }).join("") + "</div>";
  }

  function stepsHtml(hits) {
    const lines = [];
    (hits || []).forEach((h) => {
      String(h.text || "").split("\n").forEach((ln) => {
        if (/^\s*(\d+[.、]|[①②③④⑤])/.test(ln) && lines.length < 5) lines.push(ln.trim());
      });
    });
    if (!lines.length) return "";
    return '<div class="pv-sec">参考步骤（由命中证据原文整理，不是模型生成）</div>'
      + '<ol class="pv-steps">' + lines.map((l) => "<li>"
        + esc(l.replace(/^\s*(\d+[.、]|[①②③④⑤])\s*/, "")) + "</li>").join("") + "</ol>";
  }

  function answerHtml(r) {
    if (r.kind !== "answer") {
      const cls = r.kind === "safe" ? "pv-bad pv-bad--safe" : "pv-bad";
      return '<div class="' + cls + '"><div class="pv-bad__title">' + esc(r.title || "未查到可靠依据") + "</div>"
        + '<div class="pv-bad__body">' + esc(r.body || "") + "</div>"
        + (r.gate && r.gate.missingTerms && r.gate.missingTerms.length
          ? '<div class="pv-note">缺失要点：' + esc(r.gate.missingTerms.join(" / ")) + "</div>" : "")
        + (r.kind === "gap" ? '<a class="pv-bad__link" href="gaps.html">去知识缺口清单 →</a>' : "")
        + "</div>";
    }
    return '<div class="pv-sec">结构化检索条件</div><div class="pv-chips">' + chipsHtml(r.structured || {}) + "</div>"
      + stepsHtml(r.hits)
      + '<div class="pv-sec">命中证据（治理视角 · 可点开核对）</div>' + evidenceHtml(r.hits)
      + (r.filterNote ? '<div class="pv-note">' + esc(r.filterNote) + "</div>" : "")
      + '<div class="pv-note">抽测只做检索与证据门，不生成答案；结果不代表工程师端现场效果。</div>';
  }

  function renderHistory() {
    body.innerHTML = history.map((item) => {
      if (item.role === "q") {
        return '<div class="pv-msg pv-msg--q"><div class="pv-bubble">' + esc(item.text) + "</div></div>";
      }
      if (item.role === "loading") {
        return '<div class="pv-msg pv-msg--a"><div class="pv-note">正在检索…</div></div>';
      }
      if (item.error) {
        return '<div class="pv-msg pv-msg--a"><div class="pv-bad"><div class="pv-bad__title">抽测失败</div>'
          + '<div class="pv-bad__body">' + esc(item.error) + "</div></div></div>";
      }
      return '<div class="pv-msg pv-msg--a">' + answerHtml(item.result) + "</div>";
    }).join("");
    scrollBottom();
  }

  function alarmOf(q) {
    const m = String(q || "").match(/[a-z]\d{2,3}/i);
    return m ? m[0].toUpperCase() : "";
  }

  async function submit(text) {
    if (busy) return;
    const q = String(text == null ? input.value : text).trim();
    if (!q) {
      input.focus();
      input.classList.add("is-err");
      setTimeout(() => input.classList.remove("is-err"), 900);
      return;
    }
    if (!window.AdminAPI || !window.AdminAPI.previewAsk) {
      history.push({ role: "a", error: "接口适配层未加载" });
      renderHistory();
      return;
    }
    const model = modelEl.value.trim();
    const batch = batchEl.value.trim();
    history.push({ role: "q", text: q });
    history.push({ role: "loading" });
    input.value = "";
    busy = true;
    sendBtn.disabled = true;
    renderHistory();
    try {
      /* AdminAPI 成功直接返回数据、失败 reject（error.message），不是 {ok,data} 包裹 */
      const result = Object.assign({}, await window.AdminAPI.previewAsk({
        question: q,
        model: model || null,
        batch: batch || null,
      }));
      history.pop();
      if (result.structured) result.structured.alarm = alarmOf(q);
      history.push({ role: "a", result: result });
    } catch (err) {
      history.pop();
      history.push({
        role: "a",
        error: (err && err.message) || "请求失败",
      });
    } finally {
      busy = false;
      sendBtn.disabled = false;
      if (history.length > 8) history.splice(0, history.length - 8);
      renderHistory();
    }
  }

  function open() {
    panel.hidden = false;
    launcher.hidden = true;
    setTimeout(() => input.focus(), 60);
  }
  function close() {
    panel.hidden = true;
    launcher.hidden = false;
  }
  function clearAll() {
    history.length = 0;
    body.innerHTML = '<div class="pv-welcome"><p>已清空。抽测记录只存在内存里。</p></div>';
  }

  launcher.addEventListener("click", open);
  $("pvClose").addEventListener("click", close);
  $("pvClear").addEventListener("click", clearAll);
  sendBtn.addEventListener("click", () => submit());
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submit(); }
    if (e.key === "Escape") close();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !panel.hidden) close();
  });
  body.addEventListener("click", (e) => {
    const btn = e.target.closest(".pv-sample");
    if (btn) { input.value = btn.dataset.q; submit(btn.dataset.q); }
  });

  let enabled = true;

  window.AdminPreview = {
    isEnabled: function () { return enabled; },
    setEnabled: function (on) {
      enabled = !!on;
      root.style.display = enabled ? "" : "none";
      if (!enabled) close();
    },
    notConnected: false,
  };
})();
