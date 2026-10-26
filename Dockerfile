# 应用镜像（app + worker；数据库由 docker-compose 编排）
#
# 构建优化：
#   1. pip 依赖层单独缓存，改源码不需要重新下载 wheel；
#   2. BUILD_SHA/BUILD_TIME 放在最后，改版本号不会让依赖层失效。
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# pip 源：默认官方源；国内网络可用 build args 指向镜像（如清华）
ARG PIP_INDEX_URL=https://pypi.org/simple
ENV PIP_INDEX_URL=${PIP_INDEX_URL} \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_DEFAULT_TIMEOUT=60

# 依赖层单独缓存：先装包元数据与源码，再装依赖
COPY pyproject.toml ./
COPY app ./app
COPY modules ./modules
COPY migrations ./migrations
# 评测用例与报告：/admin/evals 在 eval_cases 表为空时回落 evals/cases.yaml
COPY evals ./evals
# 前端静态产物（design 令牌 + admin 多页站 + widget）：由 app 同源托管，零构建
COPY frontend ./frontend
# 缓存卷不进最终镜像；pip 仍会按需下载，但 wheel 不用重复下
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --upgrade pip && pip install .

# 构建信息：放在最后两行，改它只让这一层失效，不会重跑 pip install。
# 可用 --build-arg BUILD_SHA=$(git rev-parse --short HEAD) 注入；
# /health 会把它返回，便于确认运行的是哪个提交。
ARG BUILD_SHA=""
ARG BUILD_TIME=""
ENV BUILD_SHA=${BUILD_SHA} \
    BUILD_TIME=${BUILD_TIME}

EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=5 \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
