# 环境就绪 Runbook（ops-readiness）

> 代码侧 M1-M4 已全部完成并提交。本手册覆盖剩余三项**环境依赖验证**的
> 执行步骤——每一步都写明"预期看到什么"，验证结果请回填到对应小节的
> ✅ 处并注明日期与环境。
>
> 原则：这些验证没跑完之前，对外表述保持"协议层已就绪、待真实环境验证"，
> 不说"已接入"。

---

## 1. Postgres 迁移装载

**前置**：一台有 PostgreSQL ≥ 14 的机器（本机无服务端，DDL 未经执行验证——
首次执行时留意语法报错，修完回填 `scripts/pg_schema.sql` 头注）。

**前置**：psycopg 驱动已装（`pip install psycopg[binary]`）。当前代码
**还没有 Postgres 连接后端**——本节只做"数据装载"验证；应用切换存储后端
是独立工作（auth/provenance 直用 sqlite3，切换时逐表迁移）。

```bash
# 1) 导出（只读打开源库，自检=重新装载逐行比对，已在开发机通过）
python scripts/export_sqlite_to_pg.py                # 用 TRADEPILOT_DATA_DIR
# 或指定目录：
python scripts/export_sqlite_to_pg.py --data-dir /tmp/tradepilot --out /tmp/pg_migrate.sql

# 2) 建库建表
psql -h localhost -U postgres -c "CREATE DATABASE tradepilot;"
psql -h localhost -U postgres -d tradepilot -f scripts/pg_schema.sql

# 3) 装载数据（INSERT + setval 拨序列，事务包裹）
psql -h localhost -U postgres -d tradepilot -f /tmp/pg_migrate.sql
```

**验证清单**（全部通过才算 ✅）：
- [ ] 装载零报错（INSERT 行数 = 导出报告行数）
- [ ] `SELECT COUNT(*) FROM provenance;` 与源库一致
- [ ] `SELECT COUNT(*) FROM users;` / `tenants;` / `refresh_tokens;` 一致
- [ ] 序列已拨到位：`INSERT INTO provenance (kind,url,fetched_at,content_hash,payload) VALUES ('t','https://x',now(),'h','{}') RETURNING id;` 应得到 max(id)+1 而非撞主键
- [ ] 中文/单引号 payload 完整：随机抽 3 条 `payload` 与源库 diff

**结果**：＿＿＿＿（日期/环境/执行人）

---

## 2. Docker 构建验证

**前置**：有 Docker 守护进程的机器（开发机守护进程不可用——Dockerfile
多阶段构建与 compose **均未 build 验证**，语法经人工核对）。

```bash
# 1) 构建多阶段镜像（阶段 1 Node 构建 React；阶段 2 Python 运行时）
docker build -t tradepilot:latest .

# 2) 起容器（数据目录 /tmp 可写；Chromium 用系统包）
docker run --rm -p 8080:8080 tradepilot:latest

# 3) compose（凭证走 .env.local，可不存在）
docker compose up api
```

**验证清单**：
- [ ] 阶段 1 `npm ci && npm run build` 成功（React 产物进镜像）
- [ ] 阶段 2 镜像构建成功、体积记录：＿＿＿
- [ ] 容器启动后 `curl localhost:8080/api/health` 返回 `"status":"ok"`
- [ ] **`curl localhost:8080/app` 返回 React index.html**（多阶段修复的
      缺口——单阶段时代这里 404）
- [ ] `curl localhost:8080/canvas` 返回 Canvas 大屏
- [ ] `docker compose up` 后 `.env.local` 缺失也能起（LLM 如实离线）
- [ ] 容器内注册→登录→跑一次任务 → 存档落在挂载卷 `tradepilot-data`

**结果**：＿＿＿＿

---

## 3. Shopee / Lazada 真实凭证联调

**前置**：平台开发者账号 + App 审核。
- Shopee：[open.shopee.com](https://open.shopee.com) → Partner 注册 →
  Create App（Test/Production）→ 拿 Partner ID + Partner Key；
- Lazada：[open.lazada.com](https://open.lazada.com) → Create App →
  拿 App Key + App Secret。

**前置**：凭证写 `.env.local`（已在 .gitignore，**不进聊天/仓库**）：

```
SHOPEE_PARTNER_ID=...
SHOPEE_PARTNER_KEY=...
SHOPEE_REDIRECT=https://your-domain/app/integrations/callback   # 平台后台登记的回调
LAZADA_APP_KEY=...
LAZADA_APP_SECRET=...
LAZADA_REDIRECT=https://your-domain/app/integrations/callback
```

> 回调地址建议直接配本应用的**授权回调承接页** `/app/integrations/callback`：
> 卖家点完"确认授权"会被带回这里，页面自动取出 code（Shopee 回调还带
> shop_id / main_merchant_id）并完成换令牌，不需要手动复制。
> 若配到别处，也能从集成页手动粘贴 code（第二步），两条路等价。
> 注意：Shopee/Lazada 回调的 query 参数名以联调实测为准，页面缺 code 时会
> 如实提示而不猜参数。

**联调步骤**（每家独立做，互不依赖；端点已接线——
`GET /api/integrations/{platform}/auth-url`、
`POST /api/integrations/{platform}/exchange`，均需登录令牌）：

```bash
# 0) 登录拿租户令牌（授权是租户动作）
T=$(curl -s -X POST http://localhost:8080/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"...","password":"..."}' | python -c "import sys,json;print(json.load(sys.stdin)['access_token'])")

# 1) 授权链接 → 浏览器打开 → 卖家账号点"确认授权"
curl http://localhost:8080/api/integrations/shopee/auth-url -H "Authorization: Bearer $T"

# 2) 回调带 code 换店铺令牌（code 一次性、约 5 分钟有效，手速要快）
#    成功即入进程内 TOKENS（重启即清，明文永不回传）
curl -X POST http://localhost:8080/api/integrations/shopee/exchange \
  -H "Authorization: Bearer $T" -H "Content-Type: application/json" \
  -d '{"code":"从回调拿到的code"}'

# 3) 第一个真实业务调用（店铺级，带 token）
#    Shopee: /api/v2/shop/get_shop_info   Lazada: /product/get（limit=5）
```

**签名错误的排查表**（两家差异全部在 `app/datasources/marketplace_auth.py`
docstring 与 `tests/test_openapi_clients.py`）：

| 症状 | 先查什么 |
|---|---|
| Shopee `error_sign` | 时间戳是**秒**不是毫秒；签名是**小写** hex；path 不含 `?` |
| Lazada `error_sign` | 时间戳是**毫秒**；签名是**大写** hex；`access_token` 是否参与签名 |
| Lazada code `10` | 会话超时——token 过期，走 refresh（rotation 语义） |
| HTTP 200 但业务失败 | 正常信封行为：Shopee 看 `error` 字段、Lazada 看 `code` 字段，只看状态码必踩坑 |
| 429 | 客户端已有 0.2s 最小间隔 + GET 限 429/5xx 重试；仍限流说明 App 级配额不够 |

**验证清单**：
- [ ] 未配置凭证时调业务接口 → `not_configured`，不发请求（本地已测）
- [ ] 授权链接可打开、回调 code 换 token 成功
- [ ] 刷新令牌成功且旧 refresh 失效（rotation）
- [ ] 数字 `0` 的 code 处理与真实服务端行为一致（当前实现宽容按成功，
      若真实服务端报错，改回严格判定并同步测试）
- [ ] 拉到第一条真实商品数据

**结果**：＿＿＿＿

---

## 4. 上线前总检查

- [ ] 全量测试：`python -m pytest tests -q`（当前 525 全绿基线不回退）
- [ ] 前端：`cd web-react && npm run check && npm run build`
- [ ] `TRADEPILOT_JWT_SECRET` 显式配置（多实例必须）
- [ ] `TRADEPILOT_ADMIN_TOKEN` 强随机值（运营后台开关）
- [ ] `.env.local` 不在任何构建产物/镜像层里
- [ ] `/api/metrics` 的 alarms 为空
- [ ] robots 闸门开启（`TRADEPILOT_RESPECT_ROBOTS=1`）
- [ ] 存证库备份策略就位（provenance.db 是信任底座，丢了无法重建）
