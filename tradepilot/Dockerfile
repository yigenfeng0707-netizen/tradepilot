# TradePilot 容器镜像 —— 同时适配本地 Docker 与 Google Cloud Run
# 关键约束（Cloud Run）：
#   1. PORT 由平台注入，必须读取而非写死（本地 docker run 仍可用 -p 8080:8080）；
#   2. 根文件系统只读，只有 /tmp 可写 → 数据目录默认指向 /tmp（可用挂载卷覆盖）；
#   3. 无沙箱环境必须 --no-sandbox，且 /dev/shm 很小 → --disable-dev-shm-usage（见 app/tools/fetch.py）；
#   4. 必须监听 0.0.0.0 并信任反代头（Cloud Run 在容器前终止 TLS）。
#
# 多阶段构建（M3-④）：阶段 1 用 Node 构建 React 主前端产物，阶段 2 拷进
# 运行时镜像。单阶段时代只 COPY 了 web/（Canvas 大屏），镜像里没有
# web-react/dist——生产容器访问 /app（主前端）直接 404，React 主前端
# 只存在于开发机。多阶段后 dist 由 CI/构建机现场产出，不进仓库。

# ---- 阶段 1：React 主前端构建 ----
FROM node:20-slim AS web-build
WORKDIR /web
# 先只拷 package*.json 装依赖：源码一改不必重装 node_modules（层缓存）
COPY web-react/package.json web-react/package-lock.json ./
RUN npm ci
COPY web-react/ ./
RUN npm run build        # 产物在 /web/dist

# ---- 阶段 2：Python 运行时 ----
FROM python:3.11-slim

# APT_MIRROR_HOST：apt 源主机名可选替换（deb.debian.org 在部分网络不可达时用，
# 如清华源 mirrors.tuna.tsinghua.edu.cn——镜像目录结构与官方一致，只换主机名，
# 不拼路径：拼路径会得到 /debian/debian 404）。默认不换源——
# 换源是构建机网络环境的选择，不该写死在镜像里。
ARG APT_MIRROR_HOST=""

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8080 \
    TRADEPILOT_CHROMIUM=/usr/bin/chromium \
    TRADEPILOT_DATA_DIR=/tmp/tradepilot

WORKDIR /app

# 系统 chromium：比 playwright install-deps 体积小且版本稳定
RUN if [ -n "$APT_MIRROR_HOST" ]; then \
      sed -i "s|deb.debian.org|$APT_MIRROR_HOST|g" /etc/apt/sources.list.d/debian.sources; \
    fi \
    && apt-get update && apt-get install -y --no-install-recommends \
    chromium \
    curl \
    ca-certificates \
    fonts-wqy-zenhei \
    fonts-noto-color-emoji \
    && rm -rf /var/lib/apt/lists/*

# 先复制 requirements.txt，再安装 Python 依赖（利用层缓存）
COPY requirements.txt /app/requirements.txt
RUN pip install -r /app/requirements.txt

# 复制应用代码（scripts 不进镜像：演示/评测脚本不参与线上运行）
COPY app/ /app/app/
COPY web/ /app/web/
# React 主前端构建产物（阶段 1）——/app 路由由它服务，缺了就是 404
COPY --from=web-build /web/dist /app/web-react/dist

RUN mkdir -p /tmp/tradepilot/missions

EXPOSE 8080

# 健康检查用 python 标准库，不依赖 curl/wget（基础镜像里没装，装了也只是多一层风险）。
# 打的是 /api/health：它同时回报存证条数与数据目录，服务活着但存储没挂上的情况一并覆盖。
HEALTHCHECK --interval=30s --timeout=6s --start-period=25s --retries=3 \
  CMD python -c "import os,sys,urllib.request as u; p=os.environ.get('PORT','8080'); sys.exit(0 if u.urlopen('http://127.0.0.1:'+p+'/api/health', timeout=5).status==200 else 1)"

CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080} --proxy-headers --forwarded-allow-ips=*"]
