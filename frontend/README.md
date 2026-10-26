# frontend/ · 零构建前端

本目录是项目自己拥有的前端代码，由 `app` 同源托管；不需要 `npm install`，改完刷新即可。

## 目录与职责

```text
frontend/
├── design/
│   └── tokens.css              # 设计令牌唯一来源（两端共用）
├── admin/
│   ├── pages/                  # 后台页面结构（多页站，纯 HTML）
│   ├── styles/admin.css        # 后台样式
│   ├── controllers/            # 页面行为（渲染 / 交互 / 路由）；不直接调接口
│   └── adapters/               # 后端契约 → 视图模型；不碰 DOM
└── widget/
    ├── index.html              # 宿主演示页（模拟客户工单系统）
    ├── widget.html             # iframe 内容页（浮层面板 DOM）
    ├── loader.js               # 宿主接入层（两行 script + iframe + postMessage）
    ├── styles/widget.css       # Widget 样式
    ├── controllers/app.js      # 状态机与渲染；不直接调接口
    └── adapters/api.js         # 真实 /widget/* 与 /chat SSE 适配；不碰 DOM
```

## 三条边界（新增/修改代码时按此判断放哪）

1. **展示层**（`pages/`、`styles/`）：只负责结构、样式与文案骨架。
2. **控制器层**（`controllers/`）：只做 DOM 渲染、交互与状态流转；**禁止直接 `fetch`/`XMLHttpRequest`**，数据一律来自适配层注入的 `window.MOCK` / `window.API`。
3. **适配层**（`adapters/`）：只做接口调用与字段映射；**禁止操作 DOM**。接口形状变了只改这里。

行业词汇（欢迎语、示例问题、品牌色、安全词表、转人工文案）不在本目录硬编码，由后端配置注入。

## 服务端挂载

| URL | 目录 | 说明 |
|---|---|---|
| `/admin-app/` | `frontend/admin/pages/` | 后台多页站；未登录跳 `login.html` |
| `/widget-app/` | `frontend/widget/` | 宿主演示页 + `widget.html` 面板 + `loader.js` |
| `/ui-design/` | `frontend/design/` | 共用令牌 `tokens.css`（两端页面先加载它） |

## 新增一个后台页面

1. 在 `admin/pages/` 下新建页面，`<head>` 先引 `/ui-design/tokens.css`，再引 `../styles/admin.css`；
2. 行为放 `admin/controllers/`，数据映射放 `admin/adapters/real-api.js`；
3. 后端接口未就绪时**不挂导航、不加假数据**，等接口可用再在 `controllers/layout.js` 注册入口；
4. 补对应测试。

## 为什么没有 `shared/` 组件层

当前两个端的 DOM、图标集与交互并不共用，唯一真正共用的是设计令牌（颜色/字体/状态色）。
按 Rule of Three，等第三个消费者出现再抽公共组件；过早抽象会把两端的不同视觉强行合并。
