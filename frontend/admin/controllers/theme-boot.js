/* 首屏主题恢复：必须在任何后台页面绘制前执行，避免"先绿后蓝"闪烁。
 * 缓存由 UI.applyBrandTheme(hex, true) 在配置加载/保存后写入；没有缓存时保持默认。 */
(function () {
  "use strict";
  try {
    var raw = window.localStorage && window.localStorage.getItem("moqian_admin_theme");
    if (!raw) return;
    var t = JSON.parse(raw);
    if (!t || !/^#[0-9a-fA-F]{6}$/.test(t.green || "")) return;
    var s = document.documentElement.style;
    ["green", "green-dark", "green-soft", "green-line", "green-rgb"].forEach(function (key) {
      var value = t[key];
      if (typeof value === "string" && value) s.setProperty("--" + key, value);
    });
  } catch (e) { /* 隐私模式/坏缓存：保留默认主题 */ }
})();
