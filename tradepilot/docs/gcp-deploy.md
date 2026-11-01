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

最省事的一条（`--source .` 让 Cloud Build 在云端构建，**本机不需要 Docker**）：

```bash
gcloud run deploy $SERVICE \
  --source . \
  --region $REGION \
  --allow-unauthenticated \
  --port 8080 \
  --cpu 1 --memory 2Gi \
  --timeout 1800 \
  --concurrency 1 \
  --min-instances 0 --max-instances 2 \
  --set-env-vars TRADEPILOT_DATA_DIR=/tmp/tradepilot,TRADEPILOT_MARKETPLACE=US,TRADEPILOT_RESPECT_ROBOTS=1 \
  --set-secrets LLM_API_KEY=tradepilot-llm-key:latest
```

若已经按第 2 节推好镜像，把 `--source .` 换成
`--image $REGION-docker.pkg.dev/$PROJECT_ID/$REPO/$SERVICE:$(git rev-parse --short HEAD)` 即可。

参数理由（被问到时能答）：

- `--memory 2Gi`：Chromium 单页约 300–600MB，1Gi 在并发下会 OOM。
- `--concurrency 1`：单个任务是 WebSocket 长连接（实测 496s），占满实例的抓取与 CPU；
  调高只会让两个任务争抢同一个 Chromium 单线程池，双双变慢。
- `--timeout 1800`：LLM 模式**单次任务实测 496s**，600s 的余量只有 100s，一次网络抖动就断；
  Cloud Run 上限 3600，这里取一半留余量。
- `--min-instances 0`：演示期成本≈0（见下方成本表）；设 1 常驻约 **$71/月**。
- `--max-instances 2`：见第 5 节，SQLite 不适合横向扩太多，也是费用上限的保险。

### 成本：演示级别几乎为零

Cloud Run 每月免费额度：18 万 vCPU-秒 + 36 万 GiB-秒 + 200 万请求。

| 场景 | 计算 | 费用 |
|---|---|---|
| 单次任务（1 vCPU × 500s + 2GiB × 500s） | 500 vCPU-秒 / 1000 GiB-秒 | **$0**（免费额度内） |
| 每月可免费跑多少次 | 360,000 GiB-秒 ÷ 1000 | **360 次**（内存是瓶颈） |
| `--min-instances 1` 常驻 | 2,628,000s × (1×$0.000024 + 2×$0.0000025) | **约 $71/月** |
| Artifact Registry 存镜像 | 约 1GB × $0.10/GB | 约 $0.1/月 |

结论：**只演示就保持 `--min-instances 0`**，冷启动 3–8s 换来的是账单为 0。
常驻实例是"上线运营"才需要的，别为了首屏快 5 秒付 $71。

### 上传上下文：`.gcloudignore` 不是可选项

`--source .` 会把整个目录打包上传。仓库根有 `.gcloudignore`，它**存在时完全替代 `.gitignore`**，
所以凭证与运行态那几条必须在里面复述一遍（`.llm_key`、`.listinggood_key`、`assets/*.db`、
`assets/missions/`、`.deploy_stash/`）。漏一条就是把本机密钥和数据传进 Cloud Storage。

验证方法（看包大小就够：本次实测 **146KB / 42 个文件**，含 `docs/img` 截图的话会是好几 MB）：

```bash
gcloud storage ls -l "gs://run-sources-$PROJECT_ID-$REGION/**"
```

### 新项目的两条一次性授权

首次部署大概率卡在这两处，**都是权限不是代码**：

**① Secret 读取（必踩）**——`Creating Revision...failed`，报
`Permission denied on secret ... for Revision service account`：

```bash
gcloud secrets add-iam-policy-binding tradepilot-llm-key \
  --member="serviceAccount:$(gcloud projects describe $PROJECT_ID --format='value(projectNumber)')-compute@developer.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"
```

补完这条不必重新构建镜像——镜像已在 Artifact Registry 里，改用 `--image ...:latest` 重发，省掉几分钟构建。

**② 不需要给 Cloud Build 单独授权**：`--source .` 会自动建
`run-sources-<project>-<region>` 桶与 `cloud-run-source-deploy` 仓库。

### Windows / Git Bash 的路径陷阱（实测踩过）

在 Git Bash 里执行 `--set-env-vars TRADEPILOT_DATA_DIR=/tmp/tradepilot`，
MSYS 会把它当路径**自动改写成** `C:/Users/你/AppData/Local/Temp/tradepilot` 再交给 gcloud。
容器里于是拿到一个 Windows 路径，数据写进容器可写层的怪目录，而不是 `/tmp` 内存盘——
`/api/health` 的 `data_dir` 会直接把这个错误暴露出来。

```bash
# 查实际值（部署后必看这一条）
gcloud run services describe $SERVICE --region $REGION \
  --format="value(spec.template.spec.containers[0].env)" | tr ';' '\n' | grep DATA_DIR
# 期望：{'name': 'TRADEPILOT_DATA_DIR', 'value': '/tmp/tradepilot'}
```

修正用 **PowerShell** 调 gcloud，绕开 MSYS 转换：

```powershell
gcloud run services update tradepilot --region asia-east1 `
  --update-env-vars TRADEPILOT_DATA_DIR=/tmp/tradepilot
```

⚠️ **不要用 `MSYS_NO_PATHCONV=1`**：它连 gcloud 自身的启动路径一起破坏，
会报 `can't open file 'D:\c\Users\...\gcloud.py'`，比原问题更难排查。

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

## 7.1 实测记录：2026-10-31 首次部署（务必先读这一节）

实际部署到的环境与结果，避免下次重复踩坑或误判"部署完就万事大吉"：

| 项 | 值 |
|---|---|
| 项目 | `tradepilot-prod-2026`（计费账号 `01F639-41F87D-A7EE48`） |
| 区域 | `asia-east1` |
| 服务 URL | `https://tradepilot-590160899175.asia-east1.run.app` |
| 配置 | 1 vCPU / 2GiB / timeout 1800s / concurrency 1 / min 0 / max 2 |
| 镜像 | Cloud Build 构建，`cloud-run-source-deploy/tradepilot:latest` |
| 密钥 | Secret Manager `tradepilot-llm-key`（服务账号已授 secretAccessor） |
| 冷启动 | 首次请求 **6.8s**（`startup-cpu-boost` 生效） |

**跑通的部分**：8 个工具全部注册、`/api/health` 200、`llm: true`、
WebSocket 任务端到端走通（248s / 7 步 / `plan_engine=llm` / `model=Atria-Dawn`）、
存证写入 5 条、合规核查 1/4 条款命中且 4/4 取得正文。

**没跑通的部分（重要）**：`market_scan` 3 次全部失败，
`dangdang` / `newegg` 均返回"拦截页或空壳"；`fx_rate` 首次 522 后重试成功。
后果是 `pricing_sandbox` / `keyword_plan` / `listing_draft` / `listing_audit`
四步在依赖图上不可执行（**这是正确的降级**：没拿到真实竞品价就不编价格带），
所以 Cloud Run 上跑不出完整 8 步演示。

**根因不是代码，是出口 IP**：

- Cloud Run 默认走**共享数据中心出口 IP**，GCP/AWS 的 IP 段是反爬体系（Cloudflare 等）的重点标记对象；
- 同一份代码在本机（杭州家宽）与 WorkBuddy 沙箱上都能抓到 Newegg 真实结果页，
  说明解析逻辑没问题，差别只在出口信誉；
- `.gov` 站点不设反爬，所以合规核查在 Cloud Run 上照常成功——这条正好反证了"网络通、只是被反爬拦"。

**能做的与不能做的**：

| 方案 | 是否有效 | 代价 |
|---|---|---|
| 换 region | ❌ 大概率无效 | GCP 各 region 都是数据中心 IP，同样被标记 |
| Serverless VPC Connector + Cloud NAT 固定 IP | ❌ 基本无效 | 固定了仍是数据中心 IP，且月费增加 |
| 住宅/移动代理池 | ✅ 有效 | 按流量计费，需要改 `fetch.py` 支持上游代理 |
| 目标站官方 API（如 Amazon SP-API） | ✅ 最正规 | 需要卖家账号与授权，见 `docs/keyword-data-sources.md` |

**因此的定位建议**（答辩口径）：

> Cloud Run 证明的是**工程可部署性**——容器化、无状态、密钥托管、自动伸缩、
> 冷启动 6.8s 全部实测通过；现场演示走的是已验证完整 8 步的托管环境。
> 抓取层受共享出口 IP 的反爬限制，生产化需要固定出口或官方 API 授权，
> 这是明确的演进项，不是已知缺陷的掩盖。

**如果一定要在 Cloud Run 上演示完整链路**：把 `TRADEPILOT_*` 的数据源切到
官方 API（或接代理），否则 `market_scan` 会一直如实报错——这是设计如此，
不要为了让演示好看而关掉闸门或塞合成数据。

## 8. 回滚

```bash
gcloud run revisions list --service $SERVICE --region $REGION
gcloud run services update-traffic $SERVICE --region $REGION --to-revisions <REV>=100
```

## 9. 顺带说明：魔搭创空间怎么办

本仓库旧版固定监听 7860。镜像已改为读取 `PORT`（默认 8080），
在魔搭部署时显式设置 `PORT=7860` 即可继续沿用原有配置，无需改代码。
