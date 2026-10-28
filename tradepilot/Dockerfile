FROM python:3.11-slim

WORKDIR /app

# 系统 chromium
RUN apt-get update && apt-get install -y --no-install-recommends \
    chromium \
    curl \
    fonts-wqy-zenhei \
    fonts-noto-color-emoji \
    && rm -rf /var/lib/apt/lists/*

# 先复制 requirements.txt，再安装 Python 依赖
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# 复制应用代码
COPY app/ /app/app/
COPY web/ /app/web/
COPY scripts/ /app/scripts/

ENV PYTHONUNBUFFERED=1
EXPOSE 7860

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "7860"]
