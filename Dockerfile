# ============================================================
# RAG Proxy 镜像（多阶段构建）
# 阶段1：Node 构建 Vue3 前端 → static/
# 阶段2：Python 运行后端，托管 static/
# ============================================================

# ---------- 阶段1：前端构建 ----------
FROM node:22-alpine AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install --no-audit --no-fund
COPY frontend/ ./
# vite.config.js 的 outDir 为 ../static，相对 /build 解析后输出到容器根 /static
RUN npm run build

# ---------- 阶段2：后端运行 ----------
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    RAGPROXY_DOCKER=1 \
    RAGPROXY_DATA_DIR=/data \
    TZ=Asia/Shanghai

WORKDIR /app

# 系统依赖：时区与 PDF 解析等运行所需基础库
RUN apt-get update && apt-get install -y --no-install-recommends \
        tzdata \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY ragproxy/ ragproxy/
COPY --from=frontend /static/ static/

# 数据目录：容器内固定 /data，通过 volume 挂载持久化
VOLUME /data
EXPOSE 8100

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8100/health', timeout=3)"

CMD ["python", "-m", "ragproxy"]
