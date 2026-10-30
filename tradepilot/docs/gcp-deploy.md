# 部署到 Google Cloud Run

> 本文只讲**能直接照抄执行的命令**与**必须知道的取舍**，不讲概念。
> 镜像已按 Cloud Run 约束改造（PORT 注入、只读根文件系统、容器内 Chromium）。

## 0. 一分钟判断：Cloud Run 适不适合这个作品

| 维度 | 现状 | 结论 |
|---|---|---|
| 长任务 | 单次任务 15–60s（规则模式），LLM 模式 150–250s | Cloud Run 请求上限 60 分钟，够用；WebSocket 也被支持 |
| 浏览器 | 每次抓取需要 Chromium（容器内已装） | 可行，但**内存必须 ≥1Gi**，建议 2Gi |
| 磁盘 | 只读根文件系统，仅 `/tmp` 可写 | 已适配（`TRADEPILOT_DATA_DIR=/tmp/tradepilot`），但**实例重启即丢** |
| 状态 | 存证库是 SQLite | 多实例/多代并发写会冲突，见第 5 节 |
| 冷启动 | 镜像含 Chromium，冷启动 3–8s | 演示前设 `--min-instances=1` |

## 1. 一次性准备

```bash
export PROJECT_ID=your-project-id
export REGION=asia-east1            # 离用户近、支持 Cloud Run
export SERVICE=tradepilot
export REPO=cloud-run

gcloud config set project $PROJECT_ID
gcloud services enable run.googleapis.com cloudbuild.googleapis.com \
  artifactregistry.googleapis.com secretmanager.googleapis.com

gcloud artifacts repositories create $REPO \
  --repository-format=docker --location=$REGION
```

## 2. 构建并推送镜像

```bash
# 用 Cloud Build（无需本地 Docker）
gcloud builds submit --tag $REGION-docker.pkg.dev/$PROJECT_ID/$REPO/$SERVICE:$(git rev-parse --short HEAD)

# 或用本地 Docker
docker build -t $REGION-docker.pkg.dev/$PROJECT_ID/$REPO/$SERVICE:local .
docker push $REGION-docker.pkg.dev/$PROJECT_ID/$REPO/$SERVICE:local
```

## 3. 密钥走 Secret Manager（绝不写进镜像或环境变量明文）

```bash
printf '%s' "$LLM_API_KEY" | gcloud secrets create tradepilot-llm-key --data-file=-
# 后续更新：gcloud secrets versions add tradepilot-llm-key --data-file=-
```

## 4. 部署

```bash
gcloud run deploy $SERVICE \
  --image $REGION-docker.pkg.dev/$PROJECT_ID/$REPO/$SERVICE:$(git rev-parse --short HEAD) \
  --region $REGION \
  --allow-unauthenticated \
  --port 8080 \
  --cpu 1 --memory 2Gi \
  --timeout 600 \
  --concurrency 4 \
  --min-instances 1 --max-instances 3 \
  --set-env-vars TRADEPILOT_DATA_DIR=/tmp/tradepilot,TRADEPILOT_MARKETPLACE=US,TRADEPILOT_RESPECT_ROBOTS=1 \
  --set-secrets LLM_API_KEY=tradepilot-llm-key:latest
```

参数理由（被问到时能答）：

- `--memory 2Gi`：Chromium 单页约 300–600MB，1Gi 在并发下会 OOM。
- `--concurrency 4`：抓取被 `per_host_min_interval_s` 与浏览器单线程池串行化，调高只会排队。
- `--timeout 600`：LLM 模式下单步可能 60–90s，七步链路需要余量。
- `--min-instances 1`：避免演示时冷启动把第一个请求拖到 8s+（有成本，正式上线可设 0）。
- `--max-instances 3`：见第 5 节，SQLite 不适合横向扩太多。

## 5. 持久化：三种方案，按场合选

Cloud Run 的 `/tmp` 是**实例内存盘**，实例回收即丢失。存证库和任务存档要跨请求/跨实例存在时：

| 方案 | 做法 | 优点 | 代价 |
|---|---|---|---|
| A. 接受临时（默认，推荐用于比赛演示） | 什么都不做 | 零成本，健康检查与导出都在单实例内可用 | 实例重建后历史任务消失；`max-instances=1` 时基本不丢 |
| B. GCS 挂载卷 | `--add-volume name=data,type=cloud-storage,bucket=...` + `--add-volume-mount volume=data,mount-path=/mnt/data`，并设 `TRADEPILOT_DATA_DIR=/mnt/data` | 任务存档跨实例保留 | **SQLite 在 GCS FUSE 上并发写不安全**，必须 `--max-instances=1`，且写延迟变高 |
| C. 换成托管数据库 | 存证库改 Firestore / Cloud SQL | 真正可扩展 | 需要改 `provenance.py` 与 `store.py` 的存储层（约半天工作量） |

答辩口径建议：**如实说 A**，并说明 B/C 是明确的演进路径——比声称"已具备生产级持久化"更可信。

## 6. 抓取相关的两个硬约束

1. **robots.txt 已做成闸门**（`app/tools/fetch.py::robots_allowed`）。实测 `walmart.com` 对通用 UA 禁止 `/search`，开启闸门后该源会**如实报错**而不是绕过。要启用美国站真实竞品价，只能走该站官方 API 授权，别在这里开后门。
2. **出口 IP**：Cloud Run 默认共享出口 IP，目标站可能限流。需要固定出口时用 Serverless VPC Connector + Cloud NAT（有额外费用）。

## 7. 上线验证清单（逐条可执行）

```bash
URL=$(gcloud run services describe $SERVICE --region $REGION --format='value(status.url)')
curl -s $URL/api/health                     # 期望 {"status":"ok", ...}
curl -s $URL/api/tools                      # 期望 8 个工具
curl -s $URL/api/provenance                 # 期望 200（404 = 线上还是旧镜像）
curl -s $URL/api/missions                   # 期望 {"missions":[]} 或历史列表
# 跑一次真实任务（WebSocket，需要 wscat 或浏览器）
open $URL                                  # 点"下达任务"，观察 6 张决策卡
curl -s "$URL/api/missions/<mission_id>/export?format=md" | head -20
```

- [ ] 决策卡出现"未选候选"一行（自主性可审计的证据）
- [ ] 审计面板给出分数与逐条修复建议
- [ ] 导出 `.md` 能下载且含七节内容（真实市场数据 / 定价沙盘 / 合规体检 / 关键词层 / Listing 文案 / 文案合规审计 / 决策实录）
- [ ] 容器日志无 `Executable doesn't exist`（Chromium 路径错误）

## 8. 回滚

```bash
gcloud run revisions list --service $SERVICE --region $REGION
gcloud run services update-traffic $SERVICE --region $REGION --to-revisions <REV>=100
```

## 9. 顺带说明：魔搭创空间怎么办

本仓库旧版固定监听 7860。镜像已改为读取 `PORT`（默认 8080），
在魔搭部署时显式设置 `PORT=7860` 即可继续沿用原有配置，无需改代码。
