/* ============================================================
 * pages/config.js — 系统配置（PRD FR-P2 / admin spec §4.8）
 *
 * 这里是"换行业只换配置"的落点：模型端点、检索参数、行业文案、安全词表，
 * 以及品牌与外观（系统名称 / 登录页产品名 / 标识 / 后台主色）。
 *
 * 与早期原型的差异：
 *   1. 数据来自 GET /admin/config（经 adapters/config-api.js），没有任何 MOCK 配置；
 *   2. 「保存」写入 app_config；已接线组（LLM/检索/安全词表/浮层文案/品牌）下一次请求生效，
 *      embedding / reranker 仍未接线，检查器必须把后端 effect 原样展示；
 *   3. 连通性测试调用真实 POST /admin/config/test-llm，展示模型名 / 耗时 / 失败原因；
 *   4. 密钥输入框只读：后台不接收明文密钥（由部署环境变量 LLM_API_KEY 管理）；
 *   5. 标识文件在浏览器内校验（SVG/PNG、≤100KB）并转为 data URL 存入品牌配置，
 *      保存后由 layout.js 与登录页渲染，不新增上传接口。
 *
 * 分工：控制器只管 DOM；HTTP 与校验在 adapters/config-api.js。
 * ============================================================ */
(function () {
  "use strict";
  window.AdminPages = window.AdminPages || {};

  var PRESETS = ["#0f7a52", "#1f6feb", "#2b5a88", "#0f766e", "#7a4ddb", "#c0392b", "#d69e2e", "#26313c"];
  var LOGO_MAX_BYTES = 100 * 1024;
  var LOGO_DATA_URL = /^data:image\/(png|svg\+xml);base64,/;
  var GROUP_LABELS = {
    llm: "大模型服务",
    embedding: "embedding 端点",
    reranker: "重排端点",
    retrieval: "检索参数",
    widget: "助手界面文案",
    safety: "安全规则词表",
    security: "安全与登录",
    preview: "后台界面入口",
    demo: "演示站开关",
    brand: "品牌与外观",
  };
  var MODE_LABELS = {
    immediate: "立即生效",
    restartRequired: "需重启生效",
    displayOnly: "仅前端展示",
    notWired: "未接线（已保存但不生效）",
  };
  var MODE_TONES = {
    immediate: "up",
    restartRequired: "warn",
    displayOnly: "warn",
    notWired: "warn",
  };

  window.AdminPages.config = function () {
    var UI = window.UI || {};
    var API = window.AdminConfigAPI;
    var $ = function (id) { return document.getElementById(id); };
    var HEX = /^#[0-9a-fA-F]{6}$/;

    var loaded = null;   // 最近一次接口响应（原样）
    var model = null;    // toFormModel 之后的页面视图模型
    var busy = false;
    var baseline = "";   // 上次载入/保存时的表单快照（判断"有没有未保存改动"）

    function esc(value) {
      if (UI.esc) return UI.esc(value);
      return String(value == null ? "" : value).replace(/[&<>"]/g, function (ch) {
        return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[ch];
      });
    }
    function toast(message) {
      if (UI.toast) UI.toast(message);
    }
    function errText(error) {
      if (!error) return "未知错误";
      return error.userMessage || error.message || (error.code ? error.code : "请求失败");
    }

    /* ---------------- 顶部：生效状态 / 运行时值 ---------------- */
    function failBox(text) {
      return '<div class="failbox">'
        + '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><path d="M12 8v4M12 16h.01"/></svg>'
        + "<div>" + text + "</div></div>";
    }

    function renderEffect() {
      var box = $("effectBox");
      if (!model) { box.innerHTML = '<div class="empty">正在读取配置…</div>'; return; }
      var effect = model.effect || {};
      var buckets = ["immediate", "restartRequired", "displayOnly", "notWired"];
      var summary = buckets.map(function (key) {
        var list = Array.isArray(effect[key]) ? effect[key] : [];
        return '<span style="margin-right:18px;white-space:nowrap">'
          + esc(MODE_LABELS[key]) + "：<b>" + list.length + "</b> 组</span>";
      }).join("");

      var detail = buckets.map(function (key) {
        var list = Array.isArray(effect[key]) ? effect[key] : [];
        if (!list.length) return "";
        return '<div class="detail__section" style="padding:8px 0 0">'
          + '<div class="detail__label">' + esc(MODE_LABELS[key]) + "（" + list.length + "）</div>"
          + '<div class="sub">' + list.map(function (group) {
            return esc(GROUP_LABELS[group] || group);
          }).join(" · ") + "</div></div>";
      }).join("");

      var groups = effect.groups || {};
      var rows = Object.keys(GROUP_LABELS).map(function (group) {
        var info = groups[group];
        if (!info) return "";
        var mode = info.mode || "notWired";
        return '<div style="display:flex;gap:10px;padding:7px 0;border-bottom:1px solid #eef2f6;align-items:flex-start">'
          + '<span style="min-width:112px;font-size:12px;color:#52606d">' + esc(GROUP_LABELS[group]) + "</span>"
          + '<span class="stat__delta ' + (MODE_TONES[mode] || "warn") + '"'
          + ' style="min-width:150px;font-size:11.5px">' + esc(MODE_LABELS[mode] || mode) + "</span>"
          + '<span class="sub" style="flex:1;margin:0">' + esc(info.note || "") + "</span>"
          + "</div>";
      }).join("");

      var notes = (model.notes || []).map(function (note) {
        return "<div>" + esc(note) + "</div>";
      }).join("");

      box.innerHTML =
        '<div class="hint"><svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/>'
        + '<path d="M12 16v-4M12 8h.01"/></svg>'
        + "<div>本页保存写入 <code>app_config</code>。生效范围如下（后端逐组标注；"
        + "已接线组在下一次请求生效，未接线组明示不生效）：</div></div>"
        + '<div style="font-size:12px;color:#52606d;padding:8px 0 4px">' + summary + "</div>"
        + detail
        + '<div class="detail__section" style="padding:10px 0 0">'
        + '<div class="detail__label">逐组说明</div>' + rows + "</div>"
        + (notes ? '<div class="hint" style="margin-top:10px">'
          + '<svg viewBox="0 0 24 24"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>'
          + "<div style=\"line-height:1.9\">" + notes + "</div></div>" : "")
        + '<div class="sub" id="savedGroupsNote" style="margin-top:10px">'
        + "已保存的组：" + (model.savedGroups.length ? esc(model.savedGroups.join(" · ")) : "无（当前全部回落到运行时值）")
        + "</div>";
    }

    function row(label, valueHtml) {
      return '<div style="display:flex;gap:10px;padding:6px 0;border-bottom:1px solid #eef2f6">'
        + '<span style="min-width:132px;font-size:12px;color:#52606d">' + esc(label) + "</span>"
        + '<span style="font-size:12px;color:#1f2933;word-break:break-all">' + valueHtml + "</span></div>";
    }

    function renderRuntime() {
      var box = $("runtimeBox");
      if (!model) { box.innerHTML = '<div class="empty">正在读取配置…</div>'; return; }
      var rt = model.runtime || {};
      var llm = rt.llm || {};
      var embedding = rt.embedding || {};
      var reranker = rt.reranker || {};
      var retrieval = rt.retrieval || {};
      var safety = rt.safety || {};
      var security = rt.security || {};
      var preview = rt.preview || {};
      var demo = rt.demo || {};
      var keyText = llm.apiKeySet
        ? "已配置（" + esc(llm.apiKeyMasked || "******") + "）"
        : "未配置";
      box.innerHTML =
        row("LLM baseUrl", esc(llm.baseUrl || "—"))
        + row("LLM model", esc(llm.model || "—"))
        + row("LLM 超时（秒）", esc(llm.timeoutSec == null ? "—" : llm.timeoutSec))
        + row("LLM 密钥", keyText)
        + row("embedding", esc((embedding.baseUrl || "—") + " · " + (embedding.model || "—")
          + " · " + (embedding.dim == null ? "—" : embedding.dim) + " 维"))
        + row("reranker", esc((reranker.provider || "—") + " · " + (reranker.model || "—")))
        + row("检索", esc("每路召回 " + (retrieval.recallPerRoute == null ? "—" : retrieval.recallPerRoute)
          + " · 融合 " + (retrieval.fusion || "—")
          + " · 取用 " + (retrieval.topK == null ? "—" : retrieval.topK)
          + " · 证据门槛 " + (retrieval.evidenceThreshold == null ? "—" : retrieval.evidenceThreshold)))
        + row("安全词表", esc((safety.words || []).length + " 条"))
        + row("登录验证码 / 自测入口", esc("验证码 "
          + (security.captchaEnabled ? "开" : "关") + " · 自测入口 "
          + (preview.enabled ? "开" : "关")))
        + row("演示站", esc(demo.envEnabled === false
          ? "部署未启用（DEMO_ENABLED=false）"
          : (demo.enabled ? "开放" : "已暂停（后台总开关关闭）")))
        + '<div class="sub" style="margin-top:8px">以上是进程内真实生效值（环境变量 + 代码常量）；'
        + "上方表单里的「配置值」保存后不会改变这里。</div>";
    }

    /* ---------------- 表单回填 ---------------- */
    /* 重排开关只表达"启不启用"：none/off/disabled 视为关闭；其它（tencent / llama_cpp）
       视为启用。从未启用过时打开开关默认 tencent（腾讯云 AI Search /v1/rerank）。 */
    var RERANKER_OFF = ["", "none", "off", "disabled"];

    function rerankerOn(provider) {
      return RERANKER_OFF.indexOf(String(provider || "").trim().toLowerCase()) < 0;
    }

    function rerankerProviderForSave() {
      if (!$("cfgRrkEnabled").checked) return "none";
      var current = String((model && model.reranker && model.reranker.provider) || "none")
        .trim()
        .toLowerCase();
      return rerankerOn(current) ? current : "tencent";
    }

    function paintRerankerState() {
      var on = $("cfgRrkEnabled").checked;
      $("cfgRrkState").textContent = on
        ? "已启用 · 精排 provider：" + rerankerProviderForSave()
        : "已关闭 · 仅 RRF 融合";
    }

    function fill() {
      $("cfgLlmUrl").value = model.llm.baseUrl;
      $("cfgLlmModel").value = model.llm.model;
      $("cfgLlmTimeout").value = model.llm.timeoutSec;
      $("cfgEmbUrl").value = model.embedding.baseUrl;
      $("cfgEmbModel").value = model.embedding.model;
      $("cfgEmbDim").value = model.embedding.dim;
      $("cfgRrkUrl").value = model.reranker.baseUrl;
      $("cfgRrkModel").value = model.reranker.model;
      $("cfgRrkEnabled").checked = rerankerOn(model.reranker.provider);
      paintRerankerState();
      $("cfgRecall").value = model.retrieval.recallPerRoute;
      $("cfgFusion").value = model.retrieval.fusion === "weighted" ? "weighted" : "RRF";
      $("cfgTopK").value = model.retrieval.topK;
      $("cfgThreshold").value = model.retrieval.evidenceThreshold;
      $("cfgWelcome").value = model.widget.welcome;
      $("cfgSamples").value = model.widget.samples.join("\n");
      $("cfgHandoff").value = model.widget.handoffText;
      $("cfgSystemName").value = model.brand.systemName;
      $("cfgProductName").value = model.brand.productName;
      $("cfgOrgName").value = model.brand.orgName;

      $("cfgLlmKey").value = "";
      $("cfgLlmKey").placeholder = model.llm.apiKeySet
        ? "已配置（" + (model.llm.apiKeyMasked || "******") + "），后台不接收修改"
        : "未配置（由部署环境变量 LLM_API_KEY 注入）";

      $("cfgPrimary").value = HEX.test(model.brand.primaryColor) ? model.brand.primaryColor : "#0f7a52";
      $("cfgPrimaryHex").value = model.brand.primaryColor;
      $("cfgBrand").value = HEX.test(model.widget.brandColor) ? model.widget.brandColor : "#0f7a52";
      $("cfgBrandHex").value = model.widget.brandColor;

      renderSwatches("primarySwatches", model.brand.primaryColor);
      renderSwatches("widgetSwatches", model.widget.brandColor);
      renderLogo();
      renderChips();
      paintBrandPreview();

      $("cfgCaptcha").checked = !!model.security.captchaEnabled;
      paintCaptchaText();
      $("cfgPreview").checked = !!model.preview.enabled;
      paintPreviewText();
      /* 演示总开关：部署没启用演示时置灰——"打开了也不会生效"比开关本身更坑 */
      $("cfgDemoEnabled").checked = !!model.demo.enabled;
      $("cfgDemoEnabled").disabled = !model.demo.envEnabled;
      paintDemoText();
    }

    /* ---------------- 品牌预览 / Logo ---------------- */
    function isLogoImage(value) {
      return LOGO_DATA_URL.test(String(value || ""));
    }

    function logoInner() {
      if (isLogoImage(model.brand.logo)) {
        return '<img src="' + esc(model.brand.logo) + '" alt="标识" />';
      }
      return '<svg viewBox="0 0 24 24"><path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/></svg>';
    }

    function renderLogo() {
      var custom = isLogoImage(model.brand.logo);
      $("logoBox").innerHTML = logoInner();
      $("logoBox").style.background = custom ? "#fff" : "";
      $("bprMark").innerHTML = logoInner();
      $("bprMark").style.background = custom ? "#fff" : model.brand.primaryColor;
      $("bprName").textContent = $("cfgSystemName").value.trim() || model.brand.systemName || "知识助手管理台";
      $("logoNote").textContent = custom
        ? "当前标识：" + (model.brand.logoFileName || "自定义图片")
        : "当前使用内置默认标识";
    }

    function paintBrandPreview() {
      $("bprName").textContent = $("cfgSystemName").value.trim() || model.brand.systemName || "知识助手管理台";
      $("bprMark").style.background = isLogoImage(model.brand.logo) ? "#fff" : model.brand.primaryColor;
      if (UI.applyBrandTheme) UI.applyBrandTheme(model.brand.primaryColor);
    }

    /* ---------------- 颜色：取色器 + 预设色板 + hex 三向同步 ---------------- */
    function renderSwatches(boxId, current) {
      var box = $(boxId);
      var palette = PRESETS.slice();
      if (HEX.test(current || "") && palette.indexOf(current.toLowerCase()) < 0) palette.push(current);
      box.innerHTML = palette.map(function (hex) {
        var on = String(hex).toLowerCase() === String(current).toLowerCase();
        return '<span class="swatch' + (on ? " is-on" : "") + '" data-hex="' + hex
          + '" style="background:' + hex + '" title="' + hex + '"></span>';
      }).join("");
      box.querySelectorAll(".swatch").forEach(function (el) {
        el.addEventListener("click", function () {
          var hex = el.dataset.hex;
          if (boxId === "primarySwatches") {
            model.brand.primaryColor = hex;
            $("cfgPrimary").value = hex;
            $("cfgPrimaryHex").value = hex;
            paintBrandPreview();
          } else {
            model.widget.brandColor = hex;
            $("cfgBrand").value = hex;
            $("cfgBrandHex").value = hex;
          }
          box.querySelectorAll(".swatch").forEach(function (item) {
            item.classList.toggle("is-on", item === el);
          });
          updateDirty();
        });
      });
    }

    function bindColor(pickerId, hexId, onChange) {
      $(pickerId).addEventListener("input", function (event) {
        $(hexId).value = event.target.value;
        onChange(event.target.value);
      });
      $(hexId).addEventListener("input", function (event) {
        var value = event.target.value.trim();
        if (!HEX.test(value)) return; // 输入到一半不打断
        $(pickerId).value = value;
        onChange(value);
      });
    }

    bindColor("cfgPrimary", "cfgPrimaryHex", function (hex) {
      model.brand.primaryColor = hex;
      paintBrandPreview();
    });
    bindColor("cfgBrand", "cfgBrandHex", function (hex) {
      model.widget.brandColor = hex;
    });

    /* ---------------- 安全词表 ---------------- */
    function renderChips() {
      var box = $("safetyChips");
      box.innerHTML = model.safety.words.map(function (word, index) {
        return '<span class="chip">' + esc(word)
          + '<button type="button" data-delword="' + index + '" title="移除">×</button></span>';
      }).join("")
        + '<span class="chip chip--add"><input id="newWord" placeholder="输入词条" '
        + 'style="border:none;outline:none;background:none;width:96px;font-size:12px" />'
        + '<button type="button" class="chip-add-btn" data-addword="1">添加</button></span>';
      var input = $("newWord");
      input.addEventListener("keydown", function (event) {
        if (event.key !== "Enter") return;
        event.preventDefault();
        commitWords(input.value);
      });
    }

    /** 支持一次粘贴多个词条（逗号/顿号/换行分隔），空词与重复词跳过。 */
    function commitWords(raw) {
      var parts = String(raw || "").split(/[,，、\n]+/).map(function (word) {
        return word.trim();
      }).filter(Boolean);
      if (!parts.length) {
        toast("请输入要添加的安全词条");
        return;
      }
      var added = [];
      parts.forEach(function (word) {
        if (model.safety.words.indexOf(word) >= 0) return;
        model.safety.words.push(word);
        added.push(word);
      });
      renderChips();
      var input = $("newWord");
      if (input) {
        input.focus();
        input.value = "";
      }
      if (added.length) {
        toast("已添加安全词条：" + added.join("、") + "（尚未保存）");
      } else {
        toast("该词条已在词表中");
      }
      updateDirty();
    }

    $("safetyChips").addEventListener("click", function (event) {
      var addButton = event.target.closest("[data-addword]");
      if (addButton) {
        var input = $("newWord");
        commitWords(input ? input.value : "");
        return;
      }
      var button = event.target.closest("[data-delword]");
      if (!button) return;
      var index = parseInt(button.dataset.delword, 10);
      var word = model.safety.words[index];
      var confirm = UI.confirm ? UI.confirm({
        title: "移除安全词条",
        tone: "danger",
        icon: "warn",
        text: "确定从安全词表移除「" + word + "」？",
        warn: "移除后该话题将不再被拦截，可能生成操作步骤（如带电检修、短接安全回路、救援、撬门、盘车）。"
          + "<br/>安全词表已接线：保存后下一次问答即按新词表拦截，请确认这是你要的行为。",
        okText: "确认移除",
        okDanger: true,
      }) : Promise.resolve(true);
      confirm.then(function (ok) {
        if (!ok) return;
        model.safety.words.splice(index, 1);
        renderChips();
        updateDirty();
        toast("已移除安全词条：" + word + "（尚未保存）");
      });
    });

    /* ---------------- 开关文案 ---------------- */
    function paintPreviewText() {
      $("cfgPreviewText").textContent = $("cfgPreview").checked
        ? "在后台右下角显示「知识自测」入口"
        : "已关闭：后台右下角不显示知识自测入口";
    }
    $("cfgPreview").addEventListener("change", function () {
      paintPreviewText();
      toast("已记录开关（仅保存与展示，入口显隐尚未读取配置）");
    });

    function paintCaptchaText() {
      $("cfgCaptchaText").textContent = $("cfgCaptcha").checked
        ? "启用登录验证码（当前仅记录，后端验证码未实现）"
        : "启用登录验证码（当前关闭，登录只校验账号密码）";
    }
    $("cfgCaptcha").addEventListener("change", function () {
      paintCaptchaText();
      toast("已记录开关（仅保存与展示，登录页不受该开关控制）");
    });

    /* 演示总开关：保存后立即生效（/widget/session 与 /chat 都读同一份 config.demo） */
    function paintDemoText() {
      var box = $("cfgDemoEnabled");
      if (!model || !model.demo.envEnabled) {
        $("cfgDemoText").textContent = "当前部署未启用演示（DEMO_ENABLED=false），开关不可用";
        return;
      }
      $("cfgDemoText").textContent = box.checked
        ? "演示开放：带入口码可正常提问"
        : "已暂停：带入口码也只显示「这个演示需要从官网入口进入」";
    }
    $("cfgDemoEnabled").addEventListener("change", function () {
      paintDemoText();
      updateDirty();
    });

    /* ---------------- Logo：浏览器内校验后转 data URL 存入品牌配置 ----------------
     * 不新增上传接口：文件不出浏览器，只把 ≤100KB 的 SVG/PNG 读成 data URL 随
     * 品牌配置（app_config.brand.logo）保存；服务端同样会校验前缀与长度。 */
    $("logoDrop").addEventListener("click", function () {
      $("logoFile").click();
    });
    $("logoFile").addEventListener("change", function (event) {
      var file = event.target.files && event.target.files[0];
      event.target.value = ""; // 允许再次选择同一个文件
      if (!file) return;
      var name = String(file.name || "");
      var byName = /\.(svg|png)$/i.test(name);
      var byType = file.type === "image/svg+xml" || file.type === "image/png";
      if (!byName && !byType) {
        toast("标识只支持 SVG / PNG 文件");
        return;
      }
      if (file.size > LOGO_MAX_BYTES) {
        toast("标识文件不能超过 100KB（当前 " + Math.ceil(file.size / 1024) + "KB）");
        return;
      }
      var reader = new FileReader();
      reader.onload = function () {
        var dataUrl = String(reader.result || "");
        if (!LOGO_DATA_URL.test(dataUrl)) {
          toast("文件内容不是有效的 SVG / PNG");
          return;
        }
        model.brand.logo = dataUrl;
        model.brand.logoFileName = name;
        renderLogo();
        paintBrandPreview();
        updateDirty();
        toast("已选择标识「" + name + "」，保存后生效");
      };
      reader.onerror = function () {
        toast("读取标识文件失败，请重试");
      };
      reader.readAsDataURL(file);
    });
    $("cfgSystemName").addEventListener("input", paintBrandPreview);
    $("cfgRrkEnabled").addEventListener("change", paintRerankerState);

    /* ---------------- 收集表单 → 视图模型 ---------------- */
    function collect() {
      return {
        llm: {
          baseUrl: $("cfgLlmUrl").value,
          model: $("cfgLlmModel").value,
          timeoutSec: $("cfgLlmTimeout").value,
        },
        embedding: {
          baseUrl: $("cfgEmbUrl").value,
          model: $("cfgEmbModel").value,
          dim: $("cfgEmbDim").value,
        },
        reranker: {
          baseUrl: $("cfgRrkUrl").value,
          model: $("cfgRrkModel").value,
          provider: rerankerProviderForSave(),
        },
        retrieval: {
          recallPerRoute: $("cfgRecall").value,
          fusion: $("cfgFusion").value,
          topK: $("cfgTopK").value,
          evidenceThreshold: $("cfgThreshold").value,
        },
        widget: {
          welcome: $("cfgWelcome").value,
          samples: $("cfgSamples").value.split("\n"),
          handoffText: $("cfgHandoff").value,
          brandColor: $("cfgBrandHex").value,
        },
        safety: { words: model.safety.words.slice() },
        security: { captchaEnabled: $("cfgCaptcha").checked },
        preview: { enabled: $("cfgPreview").checked },
        demo: { enabled: $("cfgDemoEnabled").checked },
        brand: {
          systemName: $("cfgSystemName").value,
          productName: $("cfgProductName").value,
          orgName: $("cfgOrgName").value,
          primaryColor: $("cfgPrimaryHex").value,
          logo: model.brand.logo,
          logoFileName: model.brand.logoFileName,
        },
      };
    }

    /* ---------------- 保存成功后同步"本会话可见"的外壳状态 ---------------- */
    function refreshShell() {
      var mock = window.MOCK;
      if (mock && mock.systemConfig) {
        mock.systemConfig.brand = {
          systemName: model.brand.systemName,
          productName: model.brand.productName,
          orgName: model.brand.orgName,
          primaryColor: model.brand.primaryColor,
          logo: model.brand.logo,
          logoFileName: model.brand.logoFileName,
        };
        mock.systemConfig.security = { captchaEnabled: model.security.captchaEnabled };
        mock.systemConfig.preview = { enabled: model.preview.enabled };
        mock.systemConfig.demo = { enabled: model.demo.enabled };
      }
      if (UI.applyBrandTheme) UI.applyBrandTheme(model.brand.primaryColor, true);
      if (window.AdminLayout && window.AdminLayout.refreshBrand) window.AdminLayout.refreshBrand();
      if (window.AdminPreview && typeof window.AdminPreview.setEnabled === "function") {
        window.AdminPreview.setEnabled(!!model.preview.enabled);
      }
    }

    function applyResponse(config, message) {
      loaded = config;
      model = API.toFormModel(config);
      fill();
      renderEffect();
      renderRuntime();
      refreshShell();
      baseline = formSignature();
      updateDirty();
      if (message) toast(message);
    }

    /* ---------------- 未保存改动：快照对比 ----------------
     * 为什么要这个：以前点「还原为已保存值」时如果本来就没改动，页面完全不动、
     * 只在角落弹一句 toast，看起来像"按钮没生效"。现在把差异做成可见状态：
     * 有改动 → 保存按钮旁亮「有未保存改动」；还原 → 提示到底丢弃了几处改动。 */
    function formSignature() {
      try {
        return JSON.stringify(collect());
      } catch (e) {
        return "";
      }
    }

    function isDirty() {
      return !!loaded && formSignature() !== baseline;
    }

    function updateDirty() {
      var tag = $("dirtyTag");
      if (!tag) return;
      tag.hidden = !isDirty();
    }

    document.addEventListener("input", updateDirty);
    document.addEventListener("change", updateDirty);

    function setBusy(value) {
      busy = !!value;
      ["btnSaveConfig", "btnResetConfig", "btnSaveTheme", "btnTestLlm"].forEach(function (id) {
        var el = $(id);
        if (el) el.disabled = busy;
      });
      var fields = document.querySelectorAll(".content input, .content select, .content textarea");
      fields.forEach(function (el) { el.disabled = busy; });
      // 密钥框永远只读：后台不接收明文密钥
      $("cfgLlmKey").disabled = true;
    }

    /* ---------------- 首屏加载 ---------------- */
    function showLoadError(error) {
      var text = "配置读取失败：" + esc(errText(error));
      $("effectBox").innerHTML = failBox(text);
      $("runtimeBox").innerHTML = failBox(text);
      toast("配置读取失败：" + errText(error));
    }

    function load(message) {
      setBusy(true);
      return API.load().then(function (config) {
        applyResponse(config, message);
      }).catch(showLoadError).finally(function () {
        setBusy(false);
      });
    }

    /* ---------------- 连通性测试 ---------------- */
    function renderTest(result) {
      var box = $("llmTest");
      var source = result && result.source === "app_config"
        ? "已保存的 LLM 配置"
        : "环境变量（app_config 里没有保存过 LLM 组）";
      if (result && result.ok) {
        box.innerHTML = '<div class="okbox">'
          + '<svg viewBox="0 0 24 24"><path d="M20 6 9 17l-5-5"/></svg>'
          + "<div>连接成功：模型 <b>" + esc(result.model || "（未返回）") + "</b>，"
          + "耗时 <b>" + esc(result.latencyMs == null ? "—" : result.latencyMs + " ms") + "</b>。<br/>"
          + "测试使用：" + esc(source) + "。"
          + "注意：测试通过只代表该端点可达；问答链路读取同一份已保存 LLM 配置（缺省回落环境变量）。</div></div>";
        return;
      }
      box.innerHTML = '<div class="failbox">'
        + '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><path d="M12 8v4M12 16h.01"/></svg>'
        + "<div>连接失败" + (result && result.model ? "（模型 " + esc(result.model) + "）" : "")
        + "：" + esc(result && result.error ? result.error : "后端未返回失败原因")
        + (result && result.errorCode ? "（" + esc(result.errorCode) + "）" : "")
        + "<br/>排查顺序：① 服务地址是否 OpenAI 兼容（/v1）② 密钥是否在环境变量 LLM_API_KEY 中配置"
        + " ③ 容器网络能否访问该地址 ④ 模型名是否存在。<br/>测试使用：" + esc(source) + "。</div></div>";
    }

    $("btnTestLlm").addEventListener("click", function () {
      var box = $("llmTest");
      var button = $("btnTestLlm");
      button.disabled = true;
      box.innerHTML = '<div class="hint"><svg viewBox="0 0 24 24"><path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/>'
        + '<path d="M21 3v5h-5"/></svg>正在请求后端…</div>';
      API.testLlm().then(function (result) {
        renderTest(result || { ok: false, error: "响应为空" });
      }).catch(function (error) {
        box.innerHTML = failBox("连通性测试请求失败：" + esc(errText(error)));
      }).finally(function () {
        button.disabled = busy;
      });
    });

    /* ---------------- 保存 / 还原 ---------------- */
    function saveConfig() {
      var patch;
      try {
        patch = API.buildPatch(collect());
      } catch (error) {
        toast(errText(error));
        return;
      }
      setBusy(true);
      API.save(patch).then(function (config) {
        applyResponse(config, "已保存；已接线组下一次请求生效，未接线组见顶部「生效状态」");
      }).catch(function (error) {
        toast("保存失败：" + errText(error));
      }).finally(function () {
        setBusy(false);
      });
    }

    function saveTheme() {
      var brand = collect().brand;
      var patch;
      try {
        patch = API.buildThemePatch(brand);
      } catch (error) {
        toast(errText(error));
        return;
      }
      setBusy(true);
      API.saveTheme(patch).then(function (theme) {
        model.brand = {
          systemName: theme.systemName || model.brand.systemName,
          productName: theme.productName || model.brand.productName,
          orgName: theme.orgName || model.brand.orgName,
          primaryColor: theme.primaryColor || model.brand.primaryColor,
          logo: theme.logo || model.brand.logo,
          logoFileName: theme.logoFileName || "",
        };
        fill();
        refreshShell();
        return API.load().then(function (config) {
          applyResponse(config, "品牌主题已保存（PUT /admin/theme 与 config.brand 写同一份数据）");
        });
      }).catch(function (error) {
        toast("品牌主题保存失败：" + errText(error));
      }).finally(function () {
        setBusy(false);
      });
    }

    $("btnSaveConfig").addEventListener("click", saveConfig);
    $("btnSaveTheme").addEventListener("click", saveTheme);

    $("btnResetConfig").addEventListener("click", function () {
      if (busy) {
        toast("正在保存，请等这次保存结束后再还原");
        return;
      }
      /* 没有已保存快照时不能"静默不生效"：直接重新拉一次接口 */
      if (!loaded) {
        toast("还没有读到已保存配置，正在重新读取…");
        load("已重新读取已保存配置");
        return;
      }
      var dirty = isDirty();
      model = API.toFormModel(loaded);
      fill();
      $("llmTest").innerHTML = "";
      /* 还原不只是表单：品牌主色 / 自测入口 / 运行时展示要一起回到已保存状态，
         否则会出现"输入框回去了、侧栏和入口没回去"的半还原。 */
      refreshShell();
      renderRuntime();
      baseline = formSignature();
      updateDirty();
      toast(dirty
        ? "已丢弃未保存的改动，恢复为已保存值（未提交）"
        : "当前就是已保存值，没有需要还原的改动");
    });

    /* ---------------- 配置分区页签（B 方案） ----------------
     * 9 张卡按主题分 4 组，表单全部留在 DOM 里只切显隐：
     *   - 保存仍是"保存全部配置"，未保存改动跨页签保留；
     *   - 地址栏带 #tab=model|interface|security|diagnostics，可直接深链；
     *   - 页签栏与底部保存条都粘性定位，长表单里随时可切换/保存。 */
    var TAB_HINTS = {
      model: "模型端点、向量与重排、检索参数",
      interface: "助手欢迎语、品牌名称、标识与后台入口",
      security: "登录验证码与安全规则词表",
      diagnostics: "生效状态与进程内真实运行时值",
    };
    var tabButtons = Array.prototype.slice.call(
      document.querySelectorAll("#cfgTabs button[data-tab]")
    );
    var tabPanels = Array.prototype.slice.call(
      document.querySelectorAll(".content [data-panel]")
    );
    var activeTab = "model";

    function normalizeTab(name) {
      return Object.prototype.hasOwnProperty.call(TAB_HINTS, name) ? name : "model";
    }

    function tabFromHash() {
      var raw = String(window.location.hash || "").replace(/^#/, "");
      if (raw.indexOf("tab=") === 0) raw = raw.slice(4);
      return normalizeTab(raw);
    }

    function activateTab(name, options) {
      var opts = options || {};
      activeTab = normalizeTab(name);
      tabButtons.forEach(function (btn) {
        var on = btn.dataset.tab === activeTab;
        btn.classList.toggle("on", on);
        btn.setAttribute("aria-selected", on ? "true" : "false");
      });
      tabPanels.forEach(function (card) {
        card.hidden = card.dataset.panel !== activeTab;
      });
      var hint = $("cfgTabHint");
      if (hint) hint.textContent = TAB_HINTS[activeTab];
      if (opts.scroll !== false) window.scrollTo(0, 0);
      if (opts.hash !== false) {
        try {
          window.history.replaceState(null, "", "#tab=" + activeTab);
        } catch (e) {
          window.location.hash = "tab=" + activeTab;
        }
      }
    }

    tabButtons.forEach(function (btn) {
      btn.addEventListener("click", function () { activateTab(btn.dataset.tab); });
    });
    window.addEventListener("hashchange", function () {
      var next = tabFromHash();
      if (next !== activeTab) activateTab(next, { hash: false });
    });
    activateTab(tabFromHash(), { hash: false, scroll: false });

    /* 启动：先把表单禁用，避免读取完成前的编辑被覆盖 */
    setBusy(true);
    load();
  };
})();
