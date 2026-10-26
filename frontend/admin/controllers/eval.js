/* ============================================================
 * controllers/eval.js — 评测面板（真实后端）
 *
 * 结构/样式沿用早期原型（同一套卡片与表格），差异只有数据来源：
 *   window.AdminEvalsApi（adapters/evals-api.js）→ 真实接口；本文件不做 fetch，只渲染。
 *
 * 三个不能忘的口径（与后端 / 文档一致）：
 *   ① 指标定义唯一：引用可核验率 / 拒答（漏拒、误拒分开报）/ 要点命中率 / 批次混淆率（单项）
 *   ② 有数据才填：没有人工基线或后端没测的指标显示「—」，不填估算值
 *   ③ 换模型必须重跑同一用例集；跑完与上一次同模型同用例集做回归对比
 *
 * 评测依赖真实 LLM，POST /admin/evals/run 返回 202 后轮询单次详情直到 done/failed。
 * ============================================================ */
(function () {
  "use strict";
  window.AdminPages = window.AdminPages || {};

  window.AdminPages.eval = function () {
    const UI = window.UI;
    const API = window.AdminEvalsApi;
    if (!UI || !API) return;

    const state = { cases: [], runs: [], caseTotal: 0, polling: null };
    const pct = (v) => (typeof v === "number" && isFinite(v) ? (v * 100).toFixed(0) + "%" : "—");
    const put = (key, value) => {
      const el = document.querySelector('[data-stat="' + key + '"]');
      if (el) el.textContent = value;
    };
    const latest = () => state.runs[0] || null;
    const prevRun = () => state.runs[1] || null;

    /* ---------------- 顶部四个指标 ---------------- */
    function renderStats() {
      const r = latest();
      if (!r) {
        put("citation", "—");
        put("citationNote", "尚无评测批次");
        put("refusal", "— / —");
        put("refusalNote", "漏拒（该拒没拒）/ 误拒（不该拒却拒）分开报");
        put("hit", "—");
        put("hitNote", "覆盖标准答案关键排查要点的比例");
        put("confusion", "—");
        put("confusionNote", "runner 暂未测跨批次用例对，留空不编造");
        return;
      }
      const m = r.metrics || {};
      put("citation", pct(m.citationVerifiable));
      put("citationNote", "回答附引用中，出处真实存在且与结论相关");
      put("refusal", pct(m.underRefusal) + " / " + pct(m.overRefusal));
      put("refusalNote", "漏拒（该拒没拒）/ 误拒（不该拒却拒）分开报");
      put("hit", pct(m.keyPointHit));
      put("hitNote", "覆盖标准答案关键排查要点的比例");
      put("confusion", pct(m.batchConfusion));
      put("confusionNote", "本次未采集该指标：runner 没有跨批次用例对判定，不填 0 冒充「没混淆」");
    }

    /* ---------------- 指标口径表（含人工基线双列） ---------------- */
    const METRICS = [
      { key: "citationVerifiable", name: "引用来源可核验率", desc: "回答所附引用中「出处真实存在且内容与结论相关」的比例，逐条人工核对", base: "citationVerifiable", better: "high" },
      { key: "underRefusal", name: "拒答：漏拒率", desc: "该拒答却没拒的比例（证据不足仍硬答）", base: null, better: "low" },
      { key: "overRefusal", name: "拒答：误拒率", desc: "不该拒却拒了的比例（有证据却拒答）", base: null, better: "low" },
      { key: "keyPointHit", name: "要点命中率", desc: "回答覆盖标准答案中关键排查要点的比例", base: "keyPointHit", better: "high" },
      { key: "batchConfusion", name: "批次混淆率", desc: "跨批次用例对中把一个批次的方案给了另一个批次的比例，单项报告", base: null, better: "low" },
    ];

    function renderMetrics() {
      const r = latest();
      const note = document.getElementById("runNote");
      const tbody = document.getElementById("metricRows");
      if (!r) {
        note.textContent = "暂无评测批次：点「一键跑评测」生成第一个快照";
        tbody.innerHTML = '<tr><td colspan="5" style="color:#8a97a3">'
          + "还没有评测批次。评测会真实调用线上管线与模型，提交后在本页轮询进度。</td></tr>";
        return;
      }
      note.textContent = "本次快照 " + (r.startedAt || "—") + " · " + (r.modelSnapshot || "—");
      const b = r.baseline || null;
      tbody.innerHTML = METRICS.map((mt) => {
        const value = (r.metrics || {})[mt.key];
        let baseCell = '<span style="color:#8a97a3">—（该时期未做人工基线）</span>';
        let diffCell = '<span style="color:#8a97a3">—</span>';
        if (b && mt.base && typeof b[mt.base] === "number") {
          baseCell = pct(b[mt.base]);
          if (typeof value === "number") {
            const d = value - b[mt.base];
            const good = mt.better === "high" ? d >= 0 : d <= 0;
            diffCell = '<span class="' + (good ? "is-better" : "is-worse") + '">'
              + (d >= 0 ? "+" : "") + (d * 100).toFixed(0) + " pt</span>";
          }
        }
        return '<tr>'
          + '<td style="font-weight:600">' + mt.name + "</td>"
          + '<td style="color:#52606d">' + mt.desc + "</td>"
          + '<td class="num">' + pct(value) + "</td>"
          + '<td class="num">' + baseCell + "</td>"
          + '<td class="num">' + diffCell + "</td>"
          + "</tr>";
      }).join("") + (b
        ? '<tr class="row--hl"><td style="font-weight:600">人工基线说明</td>'
          + '<td colspan="4" style="color:#52606d">' + UI.esc(b.note || "—")
          + (typeof b.avgMinutes === "number" ? " · 人工平均用时 " + b.avgMinutes + " 分钟/例" : "")
          + ' <span style="color:#8a97a3">（未使用助手的实测成绩，上线前完成）</span></td></tr>'
        : "");
    }

    /* ---------------- 用例集 ---------------- */
    function renderCases() {
      const cases = state.cases || [];
      const pairIds = {};
      cases.forEach((c) => {
        if (c.pairId) (pairIds[c.pairId] = pairIds[c.pairId] || []).push(c.id);
      });
      const paired = Object.keys(pairIds).reduce((n, k) => n + pairIds[k].length, 0);
      document.getElementById("caseNote").textContent = cases.length
        ? "共 " + state.caseTotal + " 例（本页 " + cases.length + " 例）· 含 "
          + Object.keys(pairIds).length + " 组跨批次用例对（" + paired + " 例）"
          + " · 来源：" + (state.caseSource === "table" ? "数据库" : "仓库 evals/cases.yaml")
        : "评测集为空";
      if (!cases.length) {
        document.getElementById("caseRows").innerHTML =
          '<tr><td colspan="5" style="color:#8a97a3">评测集为空：可点右上角「用例导入」上传 CSV / Excel，或用「用例重置」恢复默认用例集。</td></tr>';
        return;
      }
      document.getElementById("caseRows").innerHTML = cases.map((c) => '<tr>'
        + '<td style="font-weight:600">' + UI.esc(c.question) + "</td>"
        + '<td>' + (c.category
          ? '<span class="tag tag--gray">' + UI.esc(c.category) + "</span>"
          : '<span style="color:#8a97a3">—</span>') + "</td>"
        + "<td>" + (c.model ? UI.esc(c.model) : '<span style="color:#8a97a3">—</span>') + "</td>"
        + "<td>" + (c.pairId
          ? '<span class="tag tag--blue">对 ' + UI.esc(c.pairId)
            + (c.pairLabel ? " · " + UI.esc(c.pairLabel) : "") + "</span>"
          : '<span style="color:#8a97a3">—</span>') + "</td>"
        + "<td>" + (c.hasBaseline
          ? '<span class="tag tag--green">有</span>'
          : '<span class="tag tag--gray">无</span>') + "</td>"
        + "</tr>").join("");
    }

    /* ---------------- 快照历史 ---------------- */
    function renderRuns() {
      const runs = state.runs || [];
      const tbody = document.getElementById("runRows");
      if (!runs.length) {
        tbody.innerHTML = '<tr><td colspan="7" style="color:#8a97a3">'
          + "暂无评测批次。评测依赖真实 LLM；同模型 + 同用例集重跑用于回归，不编造提升数值。</td></tr>";
        return;
      }
      tbody.innerHTML = runs.map((r, i) => {
        const older = runs[i + 1];
        let cmp = '<span style="color:#8a97a3">—（首个快照）</span>';
        if (older && r.metrics && older.metrics) {
          const dc = r.metrics.citationVerifiable - older.metrics.citationVerifiable;
          const dh = r.metrics.keyPointHit - older.metrics.keyPointHit;
          const comparable = isFinite(dc) && isFinite(dh);
          if (comparable) {
            const same = Math.abs(dc) < 0.0001 && Math.abs(dh) < 0.0001;
            cmp = same
              ? '<span class="is-better">与上次一致</span>'
              : '<span class="' + (dc >= 0 ? "is-better" : "is-worse") + '">可核验 '
                + (dc >= 0 ? "+" : "") + (dc * 100).toFixed(0) + " pt</span>";
          }
        }
        const statusTag = r.status === "failed"
          ? '<span class="tag tag--red">失败</span> '
          : (r.status === "running" ? '<span class="tag tag--amber">运行中</span> ' : "");
        return '<tr class="' + (i === 0 ? "row--hl" : "") + '">'
          + '<td style="white-space:nowrap;color:#52606d">' + UI.esc(r.startedAt || "—") + "</td>"
          + "<td>" + statusTag + UI.esc(r.modelSnapshot || "—") + "</td>"
          + '<td class="num">' + (r.caseCount == null ? "—" : r.caseCount) + "</td>"
          + '<td class="num">' + pct((r.metrics || {}).citationVerifiable) + "</td>"
          + '<td class="num">' + pct((r.metrics || {}).keyPointHit) + "</td>"
          + '<td class="num">' + pct((r.metrics || {}).batchConfusion) + "</td>"
          + "<td>" + cmp + "</td>"
          + "</tr>";
      }).join("");
    }

    function render() {
      renderStats();
      renderMetrics();
      renderCases();
      renderRuns();
    }

    /* ---------------- 数据加载 ---------------- */
    function load() {
      return API.listEvals({ page: 1, pageSize: 100 })
        .then((data) => {
          state.cases = (data && data.cases) || [];
          state.caseTotal = data && data.caseTotal != null ? data.caseTotal : state.cases.length;
          state.caseSource = (data && data.caseSource) || "yaml";
          state.runs = (data && data.runs) || [];
          render();
        })
        .catch((ex) => {
          if (ex && ex.status === 401) return;
          UI.toast((ex && ex.message) || "评测数据加载失败");
        });
    }

    /* ---------------- 跑评测：202 后轮询单次详情 ---------------- */
    const progressBox = document.getElementById("runProgress");
    const runButton = document.getElementById("btnRunEval");

    function progressHtml(message, percent) {
      return '<div class="progress" style="width:100%"><i style="width:' + percent + '%"></i></div>'
        + '<div class="hint"><svg viewBox="0 0 24 24"><path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/>'
        + '<path d="M21 3v5h-5"/></svg><span>' + UI.esc(message) + "</span></div>";
    }

    function stopPolling() {
      if (state.polling) {
        clearInterval(state.polling);
        state.polling = null;
      }
    }

    function pollRun(runId) {
      const startedAt = Date.now();
      state.polling = setInterval(() => {
        if (Date.now() - startedAt > 30 * 60 * 1000) {
          stopPolling();
          runButton.disabled = false;
          progressBox.innerHTML = progressHtml("轮询超时：批次仍在后台运行，请稍后点「刷新」查看结果", 100);
          return;
        }
        API.getEvalRun(runId)
          .then((run) => {
            if (run.status === "running") {
              progressBox.innerHTML = progressHtml(
                "正在跑 " + (run.caseCount == null ? "…" : run.caseCount) + " 例（复用线上管线）…", 60
              );
              return;
            }
            stopPolling();
            runButton.disabled = false;
            const ok = run.status === "done";
            progressBox.innerHTML = ok
              ? '<div class="okbox"><svg viewBox="0 0 24 24"><path d="M20 6 9 17l-5-5"/></svg>'
                + "<div>评测完成：" + (run.caseCount == null ? "" : run.caseCount + " 例") + "。</div></div>"
              : '<div class="warnbar warnbar--amber"><svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/>'
                + '<path d="M12 8v4M12 16h.01"/></svg><span>评测失败：'
                + "批次已标记 failed。请检查 LLM / embedding 配置与模型服务后重试。</span></div>";
            UI.toast(ok ? "评测完成，已生成新快照" : "评测失败，请查看后端日志");
            load();
          })
          .catch((ex) => {
            if (ex && ex.status === 401) {
              stopPolling();
              return;
            }
            stopPolling();
            runButton.disabled = false;
            progressBox.innerHTML = progressHtml(
              (ex && ex.message) || "轮询评测状态失败，请点「刷新」重试", 100
            );
          });
      }, 1500);
    }

    runButton.addEventListener("click", () => {
      stopPolling();
      runButton.disabled = true;
      progressBox.innerHTML = progressHtml("正在提交评测任务…", 8);
      API.runEval({})
        .then((run) => {
          progressBox.innerHTML = progressHtml(
            "已排队 " + run.id + "（" + (run.caseCount == null ? "" : run.caseCount + " 例）…"), 20
          );
          pollRun(run.id);
        })
        .catch((ex) => {
          runButton.disabled = false;
          if (ex && ex.status === 401) return;
          progressBox.innerHTML = progressHtml(
            (ex && ex.message) || "提交评测失败，请稍后再试", 100
          );
        });
    });

    document.getElementById("btnRefreshEval").addEventListener("click", () => {
      load().then(() => UI.toast("已刷新"));
    });

    /* ---------------- 用例导入 / 重置 ---------------- */
    const importModal = document.getElementById("caseImportModal");
    const fileInput = document.getElementById("caseFileInput");
    const fileText = document.getElementById("caseFileText");
    const importError = document.getElementById("caseImportError");
    const importConfirm = document.getElementById("caseImportConfirm");
    let pendingFile = null;

    function showImportError(message) {
      importError.textContent = message;
      importError.hidden = false;
    }

    function resetImportForm() {
      pendingFile = null;
      fileInput.value = "";
      fileText.textContent = "点击选择用例文件（CSV / Excel，≤2MB）";
      importError.hidden = true;
      importError.textContent = "";
      importConfirm.disabled = true;
      importConfirm.textContent = "开始导入";
    }

    function closeImportModal() {
      importModal.hidden = true;
      resetImportForm();
    }

    function downloadTemplate(format) {
      API.caseTemplate(format).then((blob) => {
        const url = URL.createObjectURL(blob);
        const link = document.createElement("a");
        link.href = url;
        link.download = "评测用例模板." + (format === "xlsx" ? "xlsx" : "csv");
        document.body.appendChild(link);
        link.click();
        link.remove();
        setTimeout(() => URL.revokeObjectURL(url), 1500);
      }).catch((ex) => {
        if (ex && ex.status === 401) return;
        UI.toast((ex && ex.message) || "模板下载失败，请稍后再试");
      });
    }

    document.getElementById("btnUploadCases").addEventListener("click", () => {
      resetImportForm();
      importModal.hidden = false;
    });
    document.getElementById("caseImportCancel").addEventListener("click", closeImportModal);
    document.getElementById("tplCsv").addEventListener("click", (e) => {
      e.preventDefault();
      downloadTemplate("csv");
    });
    document.getElementById("tplXlsx").addEventListener("click", (e) => {
      e.preventDefault();
      downloadTemplate("xlsx");
    });
    fileInput.addEventListener("change", () => {
      pendingFile = null;
      importConfirm.disabled = true;
      importError.hidden = true;
      const file = fileInput.files && fileInput.files[0];
      if (!file) {
        fileText.textContent = "点击选择用例文件（CSV / Excel，≤2MB）";
        return;
      }
      if (!/\.(csv|xlsx)$/i.test(file.name || "")) {
        fileInput.value = "";
        fileText.textContent = "点击选择用例文件（CSV / Excel，≤2MB）";
        showImportError("只支持 .csv 或 .xlsx 文件，请先下载模板。");
        return;
      }
      if (file.size > 2 * 1024 * 1024) {
        fileInput.value = "";
        fileText.textContent = "点击选择用例文件（CSV / Excel，≤2MB）";
        showImportError("文件超过 2MB，请拆分后重试。");
        return;
      }
      pendingFile = file;
      fileText.textContent = file.name + "（" + (file.size / 1024).toFixed(1) + " KB）";
      importConfirm.disabled = false;
    });
    importConfirm.addEventListener("click", () => {
      if (!pendingFile || importConfirm.disabled) return;
      importConfirm.disabled = true;
      importConfirm.textContent = "导入中…";
      API.importCasesFile(pendingFile).then((result) => {
        closeImportModal();
        UI.toast(
          "已导入 " + result.imported + " 条（新增 " + result.created
            + " / 更新 " + result.updated + "）"
        );
        return load();
      }).catch((ex) => {
        if (ex && ex.status === 401) return;
        importConfirm.disabled = false;
        importConfirm.textContent = "开始导入";
        showImportError((ex && ex.message) || "导入失败，请检查文件格式与表头。");
      });
    });

    document.getElementById("btnImportCases").addEventListener("click", () => {
      const btn = document.getElementById("btnImportCases");
      if (btn.disabled) return;
      UI.confirm({
        title: "重置为默认评测用例集？",
        text: "将清空当前用例表（含自行导入的用例），并写入仓库默认用例 evals/cases.yaml。下一次评测会按重置后的用例集执行。",
        tone: "danger",
        okText: "重置用例",
        okDanger: true,
      }).then((ok) => {
        if (!ok) return;
        btn.disabled = true;
        API.resetCases().then((result) => {
          UI.toast(
            "已重置为默认用例集：写入 " + result.imported + " 条，清掉 " + result.removed + " 条"
          );
          return load();
        }).catch((ex) => {
          if (ex && ex.status === 401) return;
          UI.toast((ex && ex.message) || "重置失败，请稍后再试");
        }).then(() => { btn.disabled = false; });
      });
    });

    load();
  };
})();
