/* ============================================================
 * controllers/dashboard.js — 主管驾驶舱（真实后端）
 *
 * 结构/样式/图表沿用早期原型（零依赖内联 SVG + CSS）：
 *   趋势 = 渐变柱 + 面积折线；北极星 = SVG 环形；Top5 = 条形榜；时长 = 纵向柱。
 * 与本文件唯一相关的变化是数据来源：
 *   window.AdminEvalsApi（adapters/evals-api.js）→
 *   /admin/stats/overview、/admin/stats/north-star、/admin/stats/engineers、
 *   /admin/stats/recognizability、/admin/reports/weekly。
 *
 * 纪律：
 *   - 前端不自己算指标：百分比、分子/分母、趋势最后一个点全部来自后端；
 *   - 没有数据就显示「—」/空态，不拿 0 或编造值冒充；
 *   - 周报数字与看板同源：导出时按后端返回的 sections 渲染，Markdown 由 sections 生成。
 * ============================================================ */
(function () {
  "use strict";
  window.AdminPages = window.AdminPages || {};

  window.AdminPages.dashboard = function () {
    const UI = window.UI;
    const API = window.AdminEvalsApi;
    if (!UI || !API) return;

    const state = { data: null, exports: [] };

    /* 百分比格式化：非数字一律显示「—」，绝不把 undefined 变成 "NaN%" */
    const pct = (v) => (typeof v === "number" && isFinite(v) ? (v * 100).toFixed(0) + "%" : "—");
    const put = (key, value) => {
      const el = document.querySelector('[data-stat="' + key + '"]');
      if (el) el.textContent = value;
    };
    const stamp = () => {
      const d = new Date();
      const p = (n) => String(n).padStart(2, "0");
      return d.getFullYear() + "-" + p(d.getMonth() + 1) + "-" + p(d.getDate())
        + " " + p(d.getHours()) + ":" + p(d.getMinutes());
    };
    function animateBars(root) {
      const nodes = (root || document).querySelectorAll(".bar-track i[data-w]");
      requestAnimationFrame(() => {
        Array.prototype.forEach.call(nodes, (el) => { el.style.width = el.dataset.w + "%"; });
      });
    }
    function emptyBox(text) {
      return '<div class="empty" style="padding:26px 10px">'
        + '<svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/></svg>'
        + UI.esc(text) + "</div>";
    }

    /* ---------------- 接口返回 → 页面视图模型 ---------------- */
    function current() {
      const payload = state.data || {};
      const ov = payload.overview || {};
      const ns = payload.north || {};
      const eng = payload.engineers || {};
      const rec = payload.recognizability || {};
      const kpi = ov.kpi || { questions: 0, resolveRate: 0, refusalRate: 0, handoffRate: 0 };
      const north = ns.north || { asked: 0, resolved: 0 };
      const totalSessions = typeof rec.totalSessions === "number" ? rec.totalSessions : 0;
      return {
        week: ov.week || ns.week || rec.week || "—",
        kpi,
        prevKpi: ov.prevKpi || null,
        north,
        prevNorth: ns.prevNorth || null,
        /* 后端给的是当周唯一值；前端只展示，不重算 */
        resolveRate: typeof ns.resolveRate === "number" ? ns.resolveRate : kpi.resolveRate,
        sessionResolveRate: ov.sessionResolveRate,
        recognizableRate: totalSessions > 0 ? rec.recognizableRate : null,
        badCases: ov.badCases || 0,
        weekly: ov.weekly || [],
        topQuestions: ov.topQuestions || [],
        firstFixBuckets: ov.firstFixBuckets || [],
        engineers: eng.items || [],
        engineerTotal: eng.pagination ? eng.pagination.total : (eng.items || []).length,
        threshold: typeof ns.threshold === "number"
          ? ns.threshold
          : (typeof rec.threshold === "number" ? rec.threshold : 0.8),
        totalSessions,
        identifiableSessions: typeof rec.identifiableSessions === "number" ? rec.identifiableSessions : 0,
        bySource: rec.bySource || [],
      };
    }

    function deltaInt(currentValue, previousValue) {
      if (typeof previousValue !== "number") return "较上周 —";
      const d = currentValue - previousValue;
      return "较上周 " + (d >= 0 ? "+" : "") + d + " 次";
    }
    function deltaPt(currentValue, previousValue) {
      if (typeof previousValue !== "number" || typeof currentValue !== "number") return "较上周 —";
      const d = (currentValue - previousValue) * 100;
      return "较上周 " + (d >= 0 ? "+" : "") + d.toFixed(0) + " pt";
    }

    /* ---------------- KPI（与上周对比） ---------------- */
    function renderKpi(D) {
      document.getElementById("weekTag").textContent = "统计周 " + D.week;
      const k = D.kpi, p = D.prevKpi;
      const noQuestions = !k.questions;
      put("questions", noQuestions ? "—" : k.questions.toLocaleString());
      put("questionsNote", noQuestions ? "本周还没有问答数据" : deltaInt(k.questions, p && p.questions));

      const northText = D.north.asked ? pct(D.resolveRate) : "—";
      put("northStar", northText);
      put(
        "northStarNote",
        D.north.asked
          ? deltaPt(D.resolveRate, p && p.resolveRate) + "（分子/分母 " + D.north.resolved + " / "
            + D.north.asked + " 轮）"
          : "本周可识别会话还没有提问，北极星不计算"
      );

      put("refusal", noQuestions ? "—" : pct(k.refusalRate));
      put(
        "refusalNote",
        noQuestions ? "本周还没有问答数据" : deltaPt(k.refusalRate, p && p.refusalRate) + " · 拒答多为知识缺口信号"
      );

      put("handoff", noQuestions ? "—" : pct(k.handoffRate));
      put(
        "handoffNote",
        noQuestions ? "本周还没有问答数据" : deltaPt(k.handoffRate, p && p.handoffRate) + " · 拒答与安全拦截时升高"
      );

      renderRecognizable(D);
    }

    /* ---------------- 身份可识别率（数据质量指标，不是业务指标） ----------------
     * 工程师级指标只覆盖可识别会话；未识别会话仍计入会话级指标。
     * 两轨差值就是身份接入的告警器。 */
    function renderRecognizable(D) {
      const bar = document.getElementById("idBar");
      if (!bar) return;
      if (!D.totalSessions) {
        bar.innerHTML = '<div class="warnbar">'
          + '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><path d="M12 8v4M12 16h.01"/></svg>'
          + "<span>本周还没有带提问的会话，可识别率与按人指标暂无可计算数据；"
          + "有会话后这里会显示「本周身份可识别率」与未识别占比。</span></div>";
        return;
      }
      const rate = D.recognizableRate;
      const low = typeof rate === "number" && rate < D.threshold;
      bar.innerHTML = '<div class="warnbar ' + (low ? "warnbar--amber" : "warnbar--green") + '">'
        + '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><path d="M12 8v4M12 16h.01"/></svg>'
        + "<span>本周身份可识别率 <b>" + pct(rate) + "</b>（" + D.identifiableSessions + " / "
        + D.totalSessions + " 个有提问的会话；阈值 " + Math.round(D.threshold * 100) + "%）"
        + " · 未识别会话 <b>" + pct(1 - rate) + "</b>。"
        + (low
          ? "工程师级指标只覆盖可识别会话，<b>仅供参考</b>——可识别率低通常是身份接入没配好，不是没人用；会话级指标仍然全量可信。"
          : "按人指标覆盖大部分会话，可与会话级指标对照看。")
        + "</span></div>";
      if (D.bySource.length) {
        /* 来源分布只做文字补充，不额外造一张图 */
        bar.firstElementChild.setAttribute("title", "身份来源分布：" + D.bySource
          .map((item) => (item.label || item.source) + " " + item.sessions).join(" · "));
      }
    }

    /* ---------------- 北极星拆解 ---------------- */
    function northRow(label, value, note, amber) {
      const val = (typeof value === "number" && isFinite(value)) ? value : null;
      return '<div class="north-row">'
        + '<div class="north-row__top"><span>' + label + '</span><b>' + pct(val) + "</b></div>"
        + '<div class="bar-track"><i class="' + (amber ? "is-amber" : "") + '" data-w="'
        + (val == null ? 0 : Math.round(val * 100)) + '" style="width:0%"></i></div>'
        + '<div class="north-row__note">' + note + "</div></div>";
    }

    function renderNorth(D) {
      const hasNorth = D.north.asked > 0;
      const rate = hasNorth ? D.resolveRate : null;
      const recogLow = typeof D.recognizableRate === "number" && D.recognizableRate < D.threshold;

      const R = 54;
      const C = 2 * Math.PI * R;
      const ringRate = hasNorth ? D.resolveRate : 0;
      const box = document.getElementById("northBox");
      box.innerHTML = '<div class="north-head">'
        + '<svg class="donut" viewBox="0 0 138 138" role="img" aria-label="北极星：已解决标记率 ' + pct(rate) + '">'
        + '<circle class="track" cx="69" cy="69" r="' + R + '"/>'
        + '<circle class="val" cx="69" cy="69" r="' + R + '" transform="rotate(-90 69 69)" '
        + 'style="stroke-dasharray:' + C.toFixed(1) + ";stroke-dashoffset:" + C.toFixed(1) + '" '
        + 'data-offset="' + (C * (1 - ringRate)).toFixed(1) + '"/>'
        + '<text class="center" x="69" y="65" text-anchor="middle">' + pct(rate) + "</text>"
        + '<text class="center-sub" x="69" y="83" text-anchor="middle">已解决标记率</text>'
        + "</svg>"
        + '<div class="north-split">'
        + '<div class="north-split__top"><span>分子 / 分母</span><b>'
        + (hasNorth ? D.north.resolved + " / " + D.north.asked + " 轮" : "—") + "</b></div>"
        + '<div class="north-split__note">当周可识别会话的提问轮次中，被工程师标记「已解决」的轮数</div>'
        + "</div></div>" + '<div class="north-body">'
        + '<div class="north-group">同一口径的对照</div>'
        + northRow("会话级（全量无偏）", D.sessionResolveRate,
          "分母＝全部有提问的会话（含未识别），不依赖身份；身份接入异常时这一条仍然可信", false)
        + '<div class="north-group">数据质量（不是业务指标）</div>'
        + northRow("身份可识别率", D.recognizableRate,
          typeof D.recognizableRate !== "number"
            ? "本周没有可计算的会话"
            : (recogLow
              ? "低于 " + Math.round(D.threshold * 100) + "% 阈值：工程师级指标只覆盖可识别会话，仅供参考"
              : "按人指标覆盖大部分会话，可与会话级对照看"), recogLow)
        + '<div class="hint" style="margin-top:4px"><svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/>'
        + '<path d="M12 16v-4M12 8h.01"/></svg><span>北极星证明「在用」与「真解决了问题」两件事；'
        + "它依赖身份归属，所以要跟「可识别率」一起看——两轨差值大，通常说明身份接入没配好，而不是没人用。</span>"
        + "</div>"
        + "</div>";
      const ring = box.querySelector(".donut .val");
      requestAnimationFrame(() => { ring.style.strokeDashoffset = ring.dataset.offset; });
    }

    /* ---------------- 趋势图：渐变柱 + 面积折线 ---------------- */
    function trendChart(rows) {
      if (!rows.length) return emptyBox("暂无趋势数据");
      const W = 640, H = 200, L = 46, R = 50, T = 16, B = 30;
      const iw = W - L - R, ih = H - T - B;
      const maxQ = Math.max(1, Math.max.apply(null, rows.map((row) => row.questions || 0)));
      const qTop = Math.max(10, Math.ceil(maxQ / 10) * 10);
      const step = iw / rows.length;
      const bw = Math.max(16, step * 0.46);
      const cx = (i) => L + step * i + step / 2;
      const yQ = (v) => T + ih - (v / qTop) * ih;
      const yR = (v) => T + ih - v * ih;

      const gridLines = [0, 0.25, 0.5, 0.75, 1].map((f) => {
        const gy = T + ih - f * ih;
        return '<line class="grid" x1="' + L + '" y1="' + gy.toFixed(1) + '" x2="' + (L + iw) + '" y2="' + gy.toFixed(1) + '"/>'
          + '<text x="' + (L - 8) + '" y="' + (gy + 3.5).toFixed(1) + '" text-anchor="end">' + Math.round(qTop * f) + "</text>"
          + '<text x="' + (L + iw + 10) + '" y="' + (gy + 3.5).toFixed(1) + '">' + Math.round(f * 100) + "%</text>";
      }).join("");

      const bars = rows.map((row, i) => {
        const by = yQ(row.questions || 0);
        return "<g><title>" + row.week + "：提问 " + (row.questions || 0) + " 次 · 已解决标记率 "
          + pct(row.resolveRate) + "</title>"
          + '<rect class="bar" x="' + (cx(i) - bw / 2).toFixed(1) + '" y="' + by.toFixed(1)
          + '" width="' + bw.toFixed(1) + '" height="' + (T + ih - by).toFixed(1) + '" rx="4"'
          + ' fill="url(#trendBar)" style="animation-delay:' + (i * 60) + 'ms"/></g>';
      }).join("");

      const pts = rows.map((row, i) => cx(i).toFixed(1) + "," + yR(row.resolveRate || 0).toFixed(1));
      const area = "M " + cx(0).toFixed(1) + "," + (T + ih) + " L " + pts.join(" L ")
        + " L " + cx(rows.length - 1).toFixed(1) + "," + (T + ih) + " Z";
      const dots = rows.map((row, i) => '<circle class="dot" cx="' + cx(i).toFixed(1) + '" cy="'
        + yR(row.resolveRate || 0).toFixed(1) + '" r="4" style="--d:' + (0.95 + i * 0.06).toFixed(2) + 's">'
        + "<title>" + row.week + " 已解决标记率 " + pct(row.resolveRate) + "</title></circle>").join("");
      const labels = rows.map((row, i) => '<text x="' + cx(i).toFixed(1) + '" y="' + (H - 10)
        + '" text-anchor="middle">' + row.week + "</text>").join("");
      const last = rows[rows.length - 1];

      return '<svg class="chart" viewBox="0 0 ' + W + " " + H + '" role="img" aria-label="近六周提问量与已解决标记率趋势">'
        + "<defs>"
        + '<linearGradient id="trendBar" x1="0" y1="0" x2="0" y2="1">'
        + '<stop offset="0%" style="stop-color:var(--green);stop-opacity:.95"/>'
        + '<stop offset="100%" style="stop-color:var(--green);stop-opacity:.35"/></linearGradient>'
        + '<linearGradient id="trendArea" x1="0" y1="0" x2="0" y2="1">'
        + '<stop offset="0%" style="stop-color:var(--green);stop-opacity:.24"/>'
        + '<stop offset="100%" style="stop-color:var(--green);stop-opacity:0"/></linearGradient>'
        + "</defs>"
        + gridLines
        + '<path class="area" d="' + area + '" style="fill:url(#trendArea)"/>'
        + bars
        + '<polyline class="line" points="' + pts.join(" ") + '" style="--len:2600"/>'
        + dots + labels
        + "</svg>"
        + '<div class="metric-legend">'
        + "<span><b>柱：</b>提问量（左轴）</span>"
        + "<span><b>折线：</b>已解决标记率（右轴，北极星）</span>"
        + "<span>本周 " + (last.questions || 0) + " 次 · " + pct(last.resolveRate) + "</span>"
        + "<span style='color:#8a97a3'>鼠标悬停柱子或圆点可看当期数值</span>"
        + "</div>";
    }

    /* ---------------- 纵向柱（首次定位时长分布） ---------------- */
    function vbarChart(rows) {
      if (!rows.length) return emptyBox("暂无定位时长数据");
      const W = 640, H = 200, L = 46, R = 14, T = 22, B = 34;
      const iw = W - L - R, ih = H - T - B;
      const max = Math.max(1, Math.max.apply(null, rows.map((row) => row.count || 0)));
      const top = Math.max(5, Math.ceil(max / 5) * 5);
      const step = iw / rows.length;
      const bw = Math.min(66, step * 0.46);
      const y = (v) => T + ih - (v / top) * ih;

      const gridLines = [0, 0.25, 0.5, 0.75, 1].map((f) => {
        const gy = T + ih - f * ih;
        return '<line class="grid" x1="' + L + '" y1="' + gy.toFixed(1) + '" x2="' + (L + iw) + '" y2="' + gy.toFixed(1) + '"/>'
          + '<text x="' + (L - 8) + '" y="' + (gy + 3.5).toFixed(1) + '" text-anchor="end">' + Math.round(top * f) + "</text>";
      }).join("");

      const total = rows.reduce((n, row) => n + (row.count || 0), 0);
      const bars = rows.map((row, i) => {
        const c = L + step * i + step / 2;
        const by = y(row.count || 0);
        return "<g><title>" + row.label + "：" + (row.count || 0) + " 例"
          + (total ? "（占 " + Math.round(((row.count || 0) / total) * 100) + "%）" : "") + "</title>"
          + '<rect class="vbar" x="' + (c - bw / 2).toFixed(1) + '" y="' + by.toFixed(1) + '" width="' + bw.toFixed(1)
          + '" height="' + (T + ih - by).toFixed(1) + '" rx="4" fill="url(#vbarGrad)"'
          + ' style="animation-delay:' + (i * 70) + 'ms"/>'
          + '<text class="vlabel" x="' + c.toFixed(1) + '" y="' + (by - 7).toFixed(1) + '" text-anchor="middle">' + (row.count || 0) + "</text>"
          + '<text x="' + c.toFixed(1) + '" y="' + (H - 12) + '" text-anchor="middle">' + row.label + "</text></g>";
      }).join("");

      return '<svg class="chart" viewBox="0 0 ' + W + " " + H + '" role="img" aria-label="首次定位故障时间分布">'
        + '<defs><linearGradient id="vbarGrad" x1="0" y1="0" x2="0" y2="1">'
        + '<stop offset="0%" style="stop-color:var(--green);stop-opacity:.95"/>'
        + '<stop offset="100%" style="stop-color:var(--green);stop-opacity:.4"/></linearGradient></defs>'
        + gridLines + bars
        + "</svg>"
        + '<div class="metric-legend">'
        + "<span><b>共 " + total + " 例</b>已解决问答的定位耗时分布</span>"
        + "<span>起点=首次提问，终点=「已解决」标记，中间含线下动手时间</span>"
        + "</div>";
    }

    /* ---------------- 条形榜（高频问题） ---------------- */
    function bars(rows) {
      if (!rows.length) return emptyBox("本周暂无提问");
      const max = Math.max(1, rows[0].count || 0);
      return '<div class="barlist">' + rows.map((row) => {
        const w = Math.max(4, Math.round(((row.count || 0) / max) * 100));
        return "<div><div class=\"bar-row__top\"><span>" + UI.esc(row.q)
          + (row.model ? ' <span class="sub">' + UI.esc(row.model) + "</span>" : "")
          + "</span><b>" + (row.count || 0) + " 次</b></div>"
          + '<div class="bar-track"><i data-w="' + w + '" style="width:0%"></i></div></div>';
      }).join("") + "</div>";
    }

    /* ---------------- 工程师使用统计 ---------------- */
    const STATE = {
      active: { label: "正常使用", cls: "tag--green" },
      idle: { label: "近期少用", cls: "tag--amber" },
      inactive: { label: "长期未使用", cls: "tag--red" },
    };

    function renderEngineers(D) {
      const tbody = document.getElementById("engRows");
      if (!D.engineers.length) {
        tbody.innerHTML = '<tr><td colspan="5" style="color:#8a97a3">'
          + "暂无在册工程师或本周无使用数据（身份未识别时请先看上方可识别率）。</td></tr>";
        return;
      }
      tbody.innerHTML = D.engineers.map((e) => {
        const state = STATE[e.state] || STATE.inactive;
        return "<tr>"
          + '<td style="font-weight:600">' + UI.esc(e.name) + '<div class="sub">' + UI.esc(e.account || "—") + "</div></td>"
          + '<td class="num">' + (e.questions == null ? "—" : e.questions) + "</td>"
          + '<td><div style="display:flex;align-items:center;gap:9px">'
          + '<div class="bar-track" style="flex:1;min-width:56px">'
          + '<i data-w="' + Math.round((e.resolveRate || 0) * 100) + '" style="width:0%"></i></div>'
          + '<span style="font-size:12px;font-variant-numeric:tabular-nums">' + pct(e.resolveRate) + "</span>"
          + "</div></td>"
          + '<td style="color:#52606d">' + UI.esc(e.lastActive || "—") + "</td>"
          + '<td><span class="tag ' + state.cls + '">' + state.label + "</span></td>"
          + "</tr>";
      }).join("");
    }

    /* ---------------- 周报：预览 → 复制 / 下载 ---------------- */
    function renderReports() {
      const tbody = document.getElementById("reportRows");
      if (!state.exports.length) {
        tbody.innerHTML = '<tr><td colspan="4" style="color:#8a97a3">本会话尚未导出周报'
          + "（导出记录不写数据库，刷新后清空）</td></tr>";
        return;
      }
      tbody.innerHTML = state.exports.map((r) => "<tr>"
        + '<td style="font-weight:600;white-space:nowrap">' + UI.esc(r.range) + "</td>"
        + '<td style="color:#52606d">' + UI.esc(r.exportedAt) + "</td>"
        + "<td>" + UI.esc(r.exportedBy) + "</td>"
        + '<td style="color:#52606d">' + UI.esc(r.format) + "</td>"
        + "</tr>").join("");
    }

    function metricValue(metric) {
      if (metric.value == null) return "—";
      const unit = metric.unit || "";
      if (typeof metric.value === "number") {
        return metric.value + (unit === "%" ? "%" : (unit ? " " + unit : ""));
      }
      return String(metric.value);
    }

    function metricDelta(metric) {
      if (typeof metric.delta !== "number" || !isFinite(metric.delta)) return "";
      if (metric.unit === "%" || metric.unit === "pt") {
        return "环比 " + (metric.delta >= 0 ? "+" : "") + metric.delta.toFixed(1) + " pt";
      }
      return "环比 " + (metric.delta >= 0 ? "+" : "") + metric.delta
        + (metric.unit ? " " + metric.unit : "");
    }

    /* 后端 WeeklyReport（sections[].metrics[]）→ 预览/下载共用的视图模型 */
    function reportModelFromApi(report) {
      const me = (window.AdminAuth && window.AdminAuth.user && window.AdminAuth.user()) || {};
      const sections = (report.sections || []).map((section) => ({
        title: section.title,
        rows: (section.metrics || []).map((metric) => [
          metric.label,
          metricValue(metric),
          metricDelta(metric) || (metric.source ? "来源：" + metric.source : ""),
        ]),
        bullets: section.body ? String(section.body).split("\n").filter((line) => line.trim()) : [],
      }));
      return {
        title: "售后知识助手 · 周报",
        sub: "统计周 " + report.week + " · 区间 " + (report.range || report.week)
          + " · 生成时间 " + stamp()
          + " · 数据来源：真实库聚合（与驾驶舱同源）"
          + (me.name ? " · 导出人 " + me.name : ""),
        sections,
      };
    }

    function reportHtml(model) {
      const rowsHtml = (rows) => '<table class="tbl report-tbl"><tbody>' + rows.map((r) =>
        '<tr><td style="width:238px;color:#52606d">' + UI.esc(r[0]) + "</td>"
        + '<td style="font-weight:600;white-space:nowrap">' + UI.esc(r[1]) + "</td>"
        + '<td style="color:#8a97a3">' + UI.esc(r[2] || "") + "</td></tr>").join("") + "</tbody></table>";
      const listHtml = (items) => items && items.length
        ? '<ul class="report-list">' + items.map((item) => "<li>" + UI.esc(item) + "</li>").join("") + "</ul>"
        : "";
      return '<div class="report-head"><h2>' + UI.esc(model.title) + "</h2>"
        + '<div class="report-sub">' + UI.esc(model.sub) + "</div></div>"
        + model.sections.map((s) => '<section class="report-sec"><h3>' + UI.esc(s.title) + "</h3>"
          + (s.rows && s.rows.length ? rowsHtml(s.rows) : "")
          + listHtml(s.bullets) + "</section>").join("");
    }

    function reportMd(model) {
      let out = "# " + model.title + "\n\n> " + model.sub + "\n";
      model.sections.forEach((s) => {
        out += "\n## " + s.title + "\n\n";
        if (s.rows) {
          s.rows.forEach((r) => {
            out += "- **" + r[0] + "**：" + r[1] + (r[2] ? "（" + r[2] + "）" : "") + "\n";
          });
        }
        if (s.bullets) s.bullets.forEach((b) => { out += "- " + b + "\n"; });
      });
      return out + "\n---\n\n本报告由服务端聚合接口生成（排除 channel=admin_preview），"
        + "数字与驾驶舱同源；未采集的指标留空，不使用估算值。\n";
    }

    function ensureReportModal() {
      let mask = document.getElementById("reportModal");
      if (mask) return mask;
      mask = document.createElement("div");
      mask.className = "modal-mask";
      mask.id = "reportModal";
      mask.hidden = true;
      mask.innerHTML = '<div class="modal report-modal">'
        + '<div class="modal__head"><svg viewBox="0 0 24 24"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="M7 10l5 5 5-5M12 15V3"/></svg>本周周报预览</div>'
        + '<div class="modal__body" id="reportBody"></div>'
        + '<div class="modal__foot">'
        + '<button class="btn btn--ghost" id="btnReportCopy" type="button">复制 Markdown</button>'
        + '<button class="btn btn--ghost" data-close type="button">关闭</button>'
        + '<button class="btn btn--primary" id="btnReportDownload" type="button">下载 .md</button>'
        + "</div></div>";
      document.body.appendChild(mask);
      return mask;
    }

    let currentExport = null;

    document.getElementById("btnExport").addEventListener("click", () => {
      const D = current();
      const btn = document.getElementById("btnExport");
      btn.disabled = true;
      document.getElementById("exportBox").innerHTML = '<div class="hint">正在向服务端请求周报聚合…</div>';
      API.weeklyReport(D.week)
        .then((report) => {
          btn.disabled = false;
          const model = reportModelFromApi(report);
          /* 优先用服务端生成的 Markdown（与 sections 同一批聚合，零重算）；
             后端未返回时再按 sections 在前端拼一份。 */
          const md = typeof report.markdown === "string" && report.markdown
            ? report.markdown
            : reportMd(model);
          const mask = ensureReportModal();
          document.getElementById("reportBody").innerHTML = reportHtml(model);
          mask.hidden = false;
          currentExport = { model, md, report };

          document.getElementById("btnReportCopy").onclick = () => {
            if (navigator.clipboard && navigator.clipboard.writeText) {
              navigator.clipboard.writeText(md).then(
                () => UI.toast("周报 Markdown 已复制到剪贴板"),
                () => UI.toast("复制失败：请手动选中预览内容复制")
              );
            } else {
              UI.toast("当前环境不支持自动复制");
            }
          };

          document.getElementById("btnReportDownload").onclick = () => {
            const name = "周报-" + report.week + ".md";
            try {
              const blob = new Blob([md], { type: "text/markdown;charset=utf-8" });
              const a = document.createElement("a");
              a.href = URL.createObjectURL(blob);
              a.download = name;
              document.body.appendChild(a);
              a.click();
              a.remove();
              setTimeout(() => URL.revokeObjectURL(a.href), 5000);
            } catch (e) {
              UI.toast("下载失败：当前环境限制了本地文件生成");
            }
            const me = (window.AdminAuth && window.AdminAuth.user && window.AdminAuth.user()) || {};
            state.exports.unshift({
              range: report.range || report.week,
              exportedAt: stamp(),
              exportedBy: me.name || me.account || "当前管理员",
              format: "Markdown（服务端聚合，与看板同源）",
            });
            renderReports();
            document.getElementById("exportBox").innerHTML =
              '<div class="okbox"><svg viewBox="0 0 24 24"><path d="M20 6 9 17l-5-5"/></svg>'
              + "<div>已生成 <b>" + UI.esc(name) + "</b>：含摘要与环比、北极星双轨与可识别率、"
              + "高频问题、定位时长分布、知识治理、身份接入质量、下周建议与口径。<br/>"
              + "<span style='color:#52606d'>只报喜的周报没有意义——缺口与坏例一并附上；"
              + "不附可识别率，数字就无法自证。</span></div></div>";
            UI.toast("周报已生成：" + name);
          };

          document.getElementById("exportBox").innerHTML =
            '<div class="hint">周报由 /admin/reports/weekly 生成（八节，与看板同源）；'
            + "可在预览里复制 Markdown 或下载 .md。</div>";
        })
        .catch((ex) => {
          btn.disabled = false;
          if (ex && ex.status === 401) return;
          document.getElementById("exportBox").innerHTML =
            '<div class="warnbar warnbar--amber"><svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/>'
            + '<path d="M12 8v4M12 16h.01"/></svg><span>周报生成失败：'
            + UI.esc((ex && ex.message) || "请稍后再试") + "</span></div>";
        });
    });

    /* ---------------- 渲染 ---------------- */
    function render() {
      const D = current();
      renderKpi(D);
      const trendBox = document.getElementById("trendBox");
      trendBox.innerHTML = trendChart(D.weekly);
      renderNorth(D);
      const topBox = document.getElementById("topBox");
      topBox.innerHTML = bars(D.topQuestions)
        + (D.topQuestions.length
          ? '<div class="hint"><svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><path d="M12 16v-4M12 8h.01"/></svg>'
            + "出现次数最高的往往就是该补的资料——与「知识缺口」清单对照着看。</div>"
          : "");
      document.getElementById("bucketBox").innerHTML = vbarChart(D.firstFixBuckets);
      renderEngineers(D);
      renderReports();
      animateBars(document);
    }

    function load() {
      const week = (function () {
        try { return new URLSearchParams(location.search).get("week") || undefined; } catch (e) { return undefined; }
      })();
      return Promise.all([
        API.statsOverview(week),
        API.northStar(week),
        API.statsEngineers(week, 1, 100),
        API.recognizability(week),
      ])
        .then(([overview, north, engineers, recognizability]) => {
          state.data = { overview, north, engineers, recognizability };
          render();
        })
        .catch((ex) => {
          if (ex && ex.status === 401) return;
          document.getElementById("weekTag").textContent = "—";
          UI.toast((ex && ex.message) || "驾驶舱数据加载失败");
        });
    }

    document.getElementById("btnRefreshDash").addEventListener("click", () => {
      load().then(() => UI.toast("已刷新"));
    });

    load();
  };
})();
