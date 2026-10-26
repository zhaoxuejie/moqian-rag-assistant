# 贡献指南

感谢愿意一起改进这个项目。提交前先看一遍下面的约定，能省掉来回。

## 环境

- Python 3.11+（CI 用 3.12）
- Docker + Docker Compose（起 Postgres / pgvector）
- 一个 OpenAI 兼容的模型端点；embedding 推荐本机 Ollama + `bge-m3`（1024 维）

## 本地跑起来

按 [README 的「🚀 快速开始」](README.md#start) 起服务。改了 `app/`、`modules/`、`frontend/`
之后要重建镜像才生效：

```powershell
docker compose up -d --build app worker
```

## 提交前自检

```powershell
ruff check .
pytest
```

没有本地 PostgreSQL 时，需要数据库的用例会自动跳过；想全跑就先 `docker compose up -d db`，
并把 `DATABASE_URL` 指到 `127.0.0.1:5433`。

## 代码约定

- **依赖单向**：`app → modules`，`modules` 之间互不 import。
- **机制 vs 行业词汇**：检索、切片、证据门这些机制放 `modules`；欢迎语、示例问题、安全词表、
  品牌由 `app` 注入，不要写死进 `modules` 或 `frontend/`。
- **契约先行**：接口有变化，先改 `contracts/openapi.yaml`，再同步前后端。
- **前端零构建**：不引 CDN、不引框架，改完刷新即可。
- **提交信息**：Conventional Commits（`feat:` / `fix:` / `docs:` / `chore:` …），
  正文写"为什么"，不要只写"改了什么"。

## PR

按 [`.github/pull_request_template.md`](.github/pull_request_template.md) 填写。一个 PR 只做一件事；
接口有变化时，契约与测试要一起出现。

## 安全

发现安全问题**不要开公开 issue**，按 [SECURITY.md](SECURITY.md) 的渠道报告。
