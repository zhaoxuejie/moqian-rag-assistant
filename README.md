# 陌阡制造业售后知识助手

[![CI](https://github.com/zhaoxuejie/moqian-service-assistant-rag-maintenance/actions/workflows/ci.yml/badge.svg)](https://github.com/zhaoxuejie/moqian-service-assistant-rag-maintenance/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-Apache--2.0-0f7a52.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-3776AB.svg)](pyproject.toml)

> 让每个新工程师背后，站着一整个售后部门的历史经验；每个答案都能核对到原文。

把故障排查从「翻手册 + 问老师傅」变成「按现象问系统」：设备手册、FAQ、历史工单被切成**证据单元**，
检索 + 证据门 + 引用校验之后给出可核对的排查步骤——每次回答强制附带手册章节号 / 工单编号，
工程师自己就能核对原文；资料里没有的，回答会明说没有，而不是编一个。

检索侧是 RAG（稠密向量 + BM25 → RRF 融合），向量存在 pgvector；多版本手册走**批次 / 版本元数据路由**，
命中冲突时并列展示并标注批次。安全话题（带电、短接、盘车、救援等）只引用安规原文，不生成操作步骤。

<p align="center">
  <a href="screenshots/widget-overview.png"><img src="screenshots/widget-overview.png" alt="工程师浮层：在工单页面按现象提问，答案带可核对引用" width="100%"></a>
</p>

<p align="center"><sub>工程师浮层：在客户工单页面按现象提问，自动带入工单与设备上下文；答案自带引用，资料里没有的会明说。</sub></p>

**Docker 一键本地部署** · 自带 30 份虚构演示语料 · Apache-2.0

## 📑 目录

- [🧠 能力一览](#overview)
- [✨ 功能清单](#features)
- [📊 评测结果](#eval)
- [🆚 与"直接丢给大模型"的区别](#why)
- [👥 适合谁 / 不适合谁](#who)
- [🖼️ 界面一览](#screens)
- [🚀 快速开始](#start)
- [⚙️ 配置](#config)
- [🏗️ 架构](#arch)
- [❓ 常见问题](#faq)
- [🤝 参与贡献](#contrib)
- [📄 许可](#license)

<a name="overview"></a>

## 🧠 能力一览

<table>
  <tr>
    <td align="center" width="33%"><b>🧠 RAG 检索</b><br><sub>稠密向量 + BM25 → RRF 融合</sub></td>
    <td align="center" width="33%"><b>🧱 pgvector</b><br><sub>向量与业务数据同库</sub></td>
    <td align="center" width="33%"><b>🚧 证据门</b><br><sub>无据不答，缺口转人工</sub></td>
  </tr>
  <tr>
    <td align="center" width="33%"><b>🔗 引用校验</b><br><sub>越界引用整条剥离</sub></td>
    <td align="center" width="33%"><b>🗂️ 批次 / 版本路由</b><br><sub>多版本并列并标注批次</sub></td>
    <td align="center" width="33%"><b>🧪 评测集</b><br><sub>同一管线跑批 + 判定脚本</sub></td>
  </tr>
</table>

---

<a name="features"></a>

## ✨ 功能清单

四组能力：工程师端的「问」、知识运营的「养」、评测治理的「证」，外加一层工程底座。

> 下面这些能力默认都可用；模型、向量化、重排都走 OpenAI 兼容端点，可随时替换。

| 🧰 问 · 工程师端 | 📚 养 · 知识运营 | 🧪 证 · 评测与治理 | ⚙️ 工程 · 部署 |
|:---|:---|:---|:---|
| 🧩 零改造接入 | 📄 多格式入库 | 🧪 评测集（作答/拒答/安全） | 🐳 Compose 一键起服务 |
| 🎯 结构化提问 | 🔐 原件 sha256 幂等 | ▶️ 一键跑批（同线上管线） | 🔌 模型可插拔 |
| ❓ 多轮追问 | ✂️ 解析与切片 | 📊 分层指标 | 🧱 零构建前端 |
| ⚡ SSE 流式回答 | 🏷️ 型号 / 批次路由 | 📝 逐题报告 + 周报 | 📜 契约优先 |
| 🔗 引用可核对 | 🗂️ 归档 / 版本 / 重解析 | 🧾 问答日志全链路 | ✅ ruff + pytest + CI |
| 👍 已解决 / 收藏 | 👁️ 原件在线预览 | 📈 主管驾驶舱 | ❤️ /health 探活 |
| ☎️ 转人工 | 🕳️ 知识缺口补课 | 👥 账号即工号 | |
| 🛡️ 安全红线 | | 💰 工单结算 | |

---

<a name="eval"></a>

## 📊 评测结果（仓库内可复现）

首批评测 80 条用例已跑完（2026-10-23，单机跑批），管线与线上 `/chat` 完全相同（同一份 `app/rag_runner.py`）：

| 指标 | 结果 |
|---|---|
| 可回答题（62 条） | 100% 作答，100% 带引用 |
| 应拒答题（9 条） | 0 条编造作答 |
| 安全红线（9 条） | 9/9 拦截 |
| 检索命中率（关键词出现在 topN 证据） | 100% |
| 时延 | p50 3.8s / p90 5.8s |

口径、样本量与逐题明细见 [`evals/reports/eval-20261023-130141.md`](evals/reports/eval-20261023-130141.md)，
用例集在 [`evals/cases.yaml`](evals/cases.yaml)，判定逻辑在 `evals/metrics.py`。

> 时延与所用模型、硬件强相关；换模型后请重跑同一套用例，报告会落在 `evals/reports/`。

---

<a name="why"></a>

## 🆚 它和"直接把手册丢给大模型"有什么不同

| 常见做法 | 本项目 |
|---|---|
| 答案看起来很对，但查不到出处 | 每条结论都带引用（PDF 页码 / Excel 行号 / 切片出处），引用由服务端校验，编造的引用整条剥离 |
| 检索不到也硬答 | 证据不足**拒答**并转人工，同时把缺口记进知识库待补 |
| 多版本手册混着答 | 命中多个批次时**并列展示并标注批次**，不替工程师做选择 |
| 安全话题生成"怎么干" | 只引用安规原文，提示联系厂家 |
| 经验散落在聊天记录 | 工单 / 收藏**人工确认**后才沉淀为新的证据单元 |

<a name="who"></a>

## 👥 适合谁 / 不适合谁

| 适合 | 不适合 |
|---|---|
| 设备售后 / 维保团队：有手册、FAQ、历史工单等可解析资料 | 资料只存在老师傅脑子里，没有可解析的文档 |
| 手册有型号 / 批次差异，需要按条件检索 | 只有一页 FAQ，答案不依赖原文 |
| 对"答案要能核对到原文"有硬要求（安规、合规、客户审计） | 只想要一个通用聊天机器人 |
| 想先本地跑通效果、再谈系统集成 | 期望开箱即用的 SaaS，不想配模型与 embedding |

<a name="screens"></a>

## 🖼️ 界面一览

> 截图均可点击查看原图。

### 🧑‍🔧 工程师浮层

浮层嵌在客户工单系统里，自动带入工单号、设备型号与批次；答案里的每条结论都能回到原文，
查不到依据时不会硬答。

<table>
  <tr>
    <td align="center" width="25%">
      <a href="screenshots/widget-2.png"><img src="screenshots/widget-2.png" alt="打开浮层" width="100%"></a><br>
      <sub><b>打开即用</b><br>示例问题由后台配置</sub>
    </td>
    <td align="center" width="25%">
      <a href="screenshots/widget-3.png"><img src="screenshots/widget-3.png" alt="安全话题拦截" width="100%"></a><br>
      <sub><b>安全话题</b><br>只引安规原文，不给操作步骤</sub>
    </td>
    <td align="center" width="25%">
      <a href="screenshots/widget-4.png"><img src="screenshots/widget-4.png" alt="无依据时转人工" width="100%"></a><br>
      <sub><b>查不到依据</b><br>转人工，并带上工单上下文</sub>
    </td>
    <td align="center" width="25%">
      <a href="screenshots/widget-5.png"><img src="screenshots/widget-5.png" alt="服务异常" width="100%"></a><br>
      <sub><b>服务异常</b><br>明确可重试 / 转人工</sub>
    </td>
  </tr>
</table>

### 🖥️ 管理后台

知识源、解析任务、问答日志、评测、驾驶舱、用户与配置，都在同一个后台里。

<table>
  <tr>
    <td align="center" width="33%">
      <a href="screenshots/admin-1.png"><img src="screenshots/admin-1.png" alt="知识源管理" width="100%"></a><br>
      <sub><b>知识源管理</b><br>30 份文档 / 195 个带出处切片</sub>
    </td>
    <td align="center" width="33%">
      <a href="screenshots/admin-2.png"><img src="screenshots/admin-2.png" alt="上传与建档" width="100%"></a><br>
      <sub><b>上传与建档</b><br>原件落盘 + 元数据标注</sub>
    </td>
    <td align="center" width="33%">
      <a href="screenshots/admin-3.png"><img src="screenshots/admin-3.png" alt="解析任务" width="100%"></a><br>
      <sub><b>解析任务</b><br>解析 → 切片 → 向量化入库</sub>
    </td>
  </tr>
  <tr>
    <td align="center" width="33%">
      <a href="screenshots/admin-4.png"><img src="screenshots/admin-4.png" alt="问答日志" width="100%"></a><br>
      <sub><b>问答日志</b><br>原问题 → 命中证据 → 引用与结果</sub>
    </td>
    <td align="center" width="33%">
      <a href="screenshots/admin-5.png"><img src="screenshots/admin-5.png" alt="评测面板" width="100%"></a><br>
      <sub><b>评测面板</b><br>换模型须重跑同一套用例</sub>
    </td>
    <td align="center" width="33%">
      <a href="screenshots/admin-6.png"><img src="screenshots/admin-6.png" alt="主管驾驶舱" width="100%"></a><br>
      <sub><b>主管驾驶舱</b><br>用没用起来、答得准不准</sub>
    </td>
  </tr>
  <tr>
    <td align="center" width="33%">
      <a href="screenshots/admin-7.png"><img src="screenshots/admin-7.png" alt="用户管理" width="100%"></a><br>
      <sub><b>用户管理</b><br>两级角色，账号即工号</sub>
    </td>
    <td align="center" width="33%">
      <a href="screenshots/admin-8.png"><img src="screenshots/admin-8.png" alt="系统配置：模型与检索" width="100%"></a><br>
      <sub><b>系统配置</b><br>模型 / 检索 / 证据门</sub>
    </td>
    <td align="center" width="33%">
      <a href="screenshots/admin-9.png"><img src="screenshots/admin-9.png" alt="系统配置：欢迎语与品牌" width="100%"></a><br>
      <sub><b>系统配置</b><br>欢迎语 / 安全词表 / 品牌</sub>
    </td>
  </tr>
</table>

<a name="start"></a>

## 🚀 快速开始

**前置**：Docker；一个 OpenAI 兼容的模型端点；一个 1024 维的 embedding 端点（推荐本机 Ollama + `bge-m3`）。

```bash
git clone https://github.com/zhaoxuejie/moqian-service-assistant-rag-maintenance.git
cd moqian-service-assistant-rag-maintenance

cp .env.example .env
# 必填：LLM_BASE_URL / LLM_MODEL / LLM_API_KEY
# 必须：ADMIN_INITIAL_PASSWORD（留空则不创建管理员账号）
# embedding 默认连本机 Ollama：http://localhost:11434/v1（容器内自动换成 host.docker.internal）

docker compose up -d --build     # db + app + worker；首次自动建表并创建管理员
docker compose ps

# 灌入自带演示语料（30 份虚构文档）
mkdir -p data/inbox
cp -r samples/demo-corpus/* data/inbox/
docker compose exec app python -m app.import_docs --root data/inbox --parse-now --no-enqueue
# --parse-now 当场同步解析，--no-enqueue 避免 worker 再排一次（按文档幂等）
```

打开：

| 入口 | 地址 | 说明 |
|---|---|---|
| 工程师浮层 | <http://localhost:8000/widget-app/> | 模拟客户工单系统 + 浮层问答 |
| 后台管理 | <http://localhost:8000/admin-app/pages/> | 账号见 `.env` 的 `ADMIN_INITIAL_ACCOUNT` / `ADMIN_INITIAL_PASSWORD` |
| 探活 | <http://localhost:8000/health> | 版本 / 构建信息 / 数据库连通性 |

Windows PowerShell 把 `cp -r` 换成 `Copy-Item samples\demo-corpus\* data\inbox\ -Recurse -Force`。

> 没有 Ollama 也能起服务、看界面；但文档入库与检索需要可用的 embedding 端点。

<a name="config"></a>

## ⚙️ 配置

完整清单见 `.env.example`，最常改的几项：

| 变量 | 作用 |
|---|---|
| `LLM_BASE_URL` / `LLM_MODEL` / `LLM_API_KEY` | 回答用的模型（OpenAI 兼容） |
| `EMBEDDING_BASE_URL` / `EMBEDDING_MODEL` / `EMBEDDING_DIM` | 向量化端点与维度（默认 1024） |
| `RETRIEVAL_*` / `EVIDENCE_*` | 召回、融合与证据门阈值 |
| `RERANKER_PROVIDER` | `none` 只用 RRF；接重排服务时再打开 |
| `JWT_SECRET` / `ADMIN_INITIAL_*` | 后台登录与初始管理员 |
| `DEMO_ENABLED` | 公开演示模式（入口码、每日配额），默认关闭 |

<a name="arch"></a>

## 🏗️ 架构

```text
app/            行业装配层：路由 / 配置 / 提示词 / RAG 编排 / 品牌
modules/        公共机制（互不依赖）：llm · tasks · doc_ingest · retrieval · evidence · citation · identity
frontend/       零构建前端：admin 多页站 + widget 浮层（同源托管）
contracts/      接口契约（openapi.yaml）
migrations/     有序 SQL 迁移
evals/          评测用例 + runner
samples/        30 份虚构演示语料
tests/          pytest
```

**模块纪律**：依赖单向 `app → modules`；modules 互不 import；每个模块可独立测试；
机制放 `modules`，行业词汇（欢迎语、示例问题、安全词表、品牌）由 `app` 注入。

```mermaid
flowchart LR
    A["工单页 / 宿主系统"] -->|注入上下文| B["浮层"]
    B -->|提问| C["/chat"]
    C --> D["结构化查询"]
    D --> E["稠密 + BM25 检索"]
    E --> F["RRF 融合"]
    F --> G{"证据门"}
    G -->|不足| H["拒答 / 转人工 / 记缺口"]
    G -->|足够| I["LLM 生成"]
    I --> J["引用校验"]
    J --> K["SSE 返回"]
    U["上传 PDF / Word / Excel"] --> P["解析 + 切片"]
    P --> W["worker 向量化"]
    W --> V[("pgvector")]
    V --> E
```

同一套检索管线也被评测 runner 复用，所以"评测结果"和"线上效果"不会分叉。

<a name="faq"></a>

## ❓ 常见问题

**一定要 Ollama 吗？**
不。`EMBEDDING_PROVIDER=openai_compatible`，任何兼容 OpenAI 的 embedding 端点都可以，
维度与 `EMBEDDING_DIM` 对齐即可。

**能换成别的行业吗？**
可以。整目录替换 `samples/demo-corpus`，在后台配置欢迎语、示例问题、安全词表与品牌即可，
代码里不写行业词。

**数据安全吗？**
数据都保存在你自己的 Postgres 里；`.env` 与 `data/` 已在 `.gitignore` 中。
不要提交密钥与手册原件。

<a name="contrib"></a>

## 🤝 参与贡献

提交前跑：

```bash
ruff check .
pytest
```

没有本地 PG 时，需要数据库的用例会自动跳过。PR 请按
[.github/pull_request_template.md](.github/pull_request_template.md) 填写。

<a name="license"></a>

## 📄 许可

[Apache-2.0](LICENSE)。

演示语料为虚构企业「宏远电梯」的模拟资料，不含真实客户数据或厂商版权文档。
