# TradePilot 容器镜像 —— 同时适配本地 Docker 与 Google Cloud Run
# 关键约束（Cloud Run）：
#   1. PORT 由平台注入，必须读取而非写死（本地 docker run 仍可用 -p 8080:8080）；
#   2. 根文件系统只读，只有 /tmp 可写 → 数据目录默认指向 /tmp（可用挂载卷覆盖）；
#   3. 无沙箱环境必须 --no-sandbox，且 /dev/shm 很小 → --disable-dev-shm-usage（见 app/tools/fetch.py）；
#   4. 必须监听 0.0.0.0 并信任反代头（Cloud Run 在容器前终止 TLS）。
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8080 \
    TRADEPILOT_CHROMIUM=/usr/bin/chromium \
    TRADEPILOT_DATA_DIR=/tmp/tradepilot

WORKDIR /app

# 系统 chromium：比 playwright install-deps 体积小且版本稳定
RUN apt-get update && apt-get install -y --no-install-recommends \
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

RUN mkdir -p /tmp/tradepilot/missions

EXPOSE 8080

# 健康检查用 python 标准库，不依赖 curl/wget（基础镜像里没装，装了也只是多一层风险）。
# 打的是 /api/health：它同时回报存证条数与数据目录，服务活着但存储没挂上的情况一并覆盖。
HEALTHCHECK --interval=30s --timeout=6s --start-period=25s --retries=3 \
  CMD python -c "import os,sys,urllib.request as u; p=os.environ.get('PORT','8080'); sys.exit(0 if u.urlopen('http://127.0.0.1:'+p+'/api/health', timeout=5).status==200 else 1)"

CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080} --proxy-headers --forwarded-allow-ips=*"]
