# 环境就绪 Runbook（ops-readiness）

> 代码侧 M1-M4 已全部完成并提交。本手册覆盖剩余三项**环境依赖验证**的
> 执行步骤——每一步都写明"预期看到什么"，验证结果请回填到对应小节的
> ✅ 处并注明日期与环境。
>
> 原则：这些验证没跑完之前，对外表述保持"协议层已就绪、待真实环境验证"，
> 不说"已接入"。

---

## 1. Postgres 迁移装载

**验证状态（2026-11-01 更新）**：DDL 已在真实 Postgres 16（docker postgres:16
容器）执行验证——7 张表建表成功、真实库导出装载逐表行数一致、provenance 序列
setval 正确、中文往返正常（quota/llm/shop 三张新表的 upsert 亦实测；方言差异
见 `scripts/pg_schema.sql` 配额节注释）。流程与结果详见
docs/production-integration.md 4.11。

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
- [x] 装载零报错（INSERT 行数 = 导出报告行数）✅ 2026-11-01，docker postgres:16 容器
- [x] `SELECT COUNT(*) FROM provenance;` 与源库一致 ✅ 同上（103 行）
- [x] `SELECT COUNT(*) FROM users;` / `tenants;` / `refresh_tokens;` 一致 ✅ 同上（18/18/20）
- [x] 序列已拨到位：迁移后新插入得 max(id)+1=104，未撞主键 ✅ 同上
- [x] 中文/单引号 payload 完整：随机抽 3 条 `payload` 与源库 diff
      ✅ 2026-11-20，md5 逐条一致（ids 23/32/41）

**结果**：2026-11-20 复验，docker postgres:16 容器（PG 16.15），
装载 2 tenants / 2 users / 0 refresh_tokens / 233 provenance，
setval 233→nextval 234，中文与单引号往返 OK，quota/usage 两次 upsert
累计正确（weight 3.0；calls 4 / prompt 172 / completion 1300），
店铺授权重复插入仍 1 行。

---

## 2. Docker 构建验证

**验证状态（2026-11-20 全面复验）**：Docker Desktop 守护进程已启用
（`29.9.3 | Docker Desktop`），本轮重新跑了完整链路：多阶段镜像 build 成功
（镜像 `ce15b88dde93`，**1.54GB**）；单容器 `/api/health`、`/app`、`/canvas`、
`/api/capabilities`、`/api/metrics` 全 200；`docker compose up` 在**无 `.env.local`**
的情况下正常起服，LLM 如实离线；注册/登录/`auth/me` 链路全通，数据落进挂载卷；
容器重启后账号仍可登录（卷持久化生效）；干净容器内补装 dev 依赖后
`pytest` **583 passed / 32.88s**。

本轮抓到并修复一个真 bug：`docker-compose.yml` 原来端口写死
`"${PORT:-8080}:8080"`，而 Dockerfile 默认 `PORT=7860`；补上容器侧
`PORT=${PORT:-8080}` 后，若用户自定义 `PORT` 仍会造成"容器监听新端口、
映射到旧端口"的错配。现在两侧统一为 `${PORT:-8080}:${PORT:-8080}`，
以 `PORT=18081` 实测通过。

此前 2026-11-01 的验证记录：多阶段镜像 build 成功，容器冒烟通过（注册 /
`/api/health` / `/app` / `/canvas` 全 200；过程中修复两个干净环境 bug：
requirements 漏 PyJWT/argon2-cffi、lifespan 漏 auth.init_db）。详见
docs/production-integration.md 4.9。

```bash
# 1) 构建多阶段镜像（阶段 1 Node 构建 React；阶段 2 Python 运行时）
docker build -t tradepilot:latest .

# 2) 起容器（数据目录 /tmp 可写；Chromium 用系统包）
#    Dockerfile 默认 7860 是魔搭创空间约束，本地用 PORT 显式覆盖
docker run --rm -p 8080:8080 -e PORT=8080 tradepilot:latest

# 3) compose（凭证走 .env.local，可不存在）
PORT=18081 docker compose -p tp-verify up -d --build
```

**验证清单**：
- [x] 阶段 1 `npm ci && npm run build` 成功（React 产物进镜像）✅ 2026-11-01 / 2026-11-20
- [x] 阶段 2 镜像构建成功 ✅ 2026-11-20，镜像大小 **1.54GB**
- [x] 容器启动后 `/api/health` 返回 ok ✅ 单容器 + Compose 双形态 200
- [x] **`/app` 返回 React index.html** ✅ 单容器 + Compose 200
- [x] `/canvas` 返回 Canvas 大屏 ✅ 单容器 + Compose 200
- [x] `docker compose up` 后 `.env.local` 缺失也能起（LLM 如实离线）✅ 2026-11-20
- [x] 容器内注册→登录→`/auth/me` → 存档落在挂载卷 `tradepilot-data`，重启后仍可登录 ✅ 2026-11-20
- [x] 干净容器内全量测试 ✅ 583 passed / 32.88s（容器内补装 requirements-dev.txt）

**结果**：2026-11-20，本机 Docker Desktop 29.9.3 + Windows，执行人：助手自动实测。

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

- [ ] 全量测试：`python -m pytest tests -q`（当前 **583** 全绿基线不回退，见
      production-integration.md 4.17）
- [ ] 前端：`cd web-react && npm run check && npm run build`
- [ ] `TRADEPILOT_JWT_SECRET` 显式配置（多实例必须）
- [ ] `TRADEPILOT_ADMIN_TOKEN` 强随机值（运营后台开关）
- [ ] `.env.local` 不在任何构建产物/镜像层里
- [ ] `/api/metrics` 的 alarms 为空
- [ ] robots 闸门开启（`TRADEPILOT_RESPECT_ROBOTS=1`）
- [ ] 存证库备份策略就位（provenance.db 是信任底座，丢了无法重建）

---

## 5. 明文密钥退役 runbook

**状态（2026-11-20）**：`.llm_key`(68B) 与 `.listinggood_key`(51B) 实体仍在；
两者已被 `.gitignore` 覆盖、从未入库，但**实体存在 != 已吊销**。

顺序不能反：

1. 在服务商网关控制台**吊销旧 Key**，留存吊销回执（时间、Key 标识）。
2. 确认线上/沙箱不再依赖旧 Key；需要时先换发新 Key 并改注入方式（env / Secret Manager）。
3. 本地执行（默认只检查；没有 `--revoked` 时 `--apply` 会被脚本拒绝）：

```bash
python scripts/retire_keys.py --check                 # 看文件名/大小/sha256 前 12 位
python scripts/retire_keys.py --revoked --apply       # 确认吊销后才真正删除
```

4. 审计记录写在 `docs/security/key-retirement.log`，只有哈希前缀与字节数，**不含密钥内容**。
5. 生产门禁 G4 会在文件仍存在时保持 FAIL，不因为"看起来没人用"而放行。

---

## 6. 生产就绪门禁

```bash
python scripts/production_gate.py collect   # 刷新 git 同步 / 人工维度 / 密钥 / 联调 / 定价 / 忽略规则
python scripts/production_gate.py check     # 8/8 才退出 0
```

8 项含义见 README「生产就绪门禁」。证据文件 `docs/production-evidence.json`
是生成物（已 gitignore），测试与 CI 的实测值由人工从 run 记录回填——
**门禁不联网猜、不拿旧结果冒充当前提交**。当前预期 FAIL 的项：G5（真实 API）、
G6（人工评测）、G7（定价）、G4（密钥实体，待 U-1 吊销后删除）。

---

## 7. 线上一致性核验（2026-11-20 18:35）

魔搭创空间部署 run `44711099929` success，headSha=`6165463cd30617ab47afa672f4eaa639a34e5084`
与本地 HEAD 完全一致。核验命令与结果：

```bash
python scripts/check_release_drift.py --base https://gsym236998-tradepilot-opc.ms.show
# 工具白名单 8/8、/api/provenance、Canvas 大屏、静态 app.js sha256 全 OK
```

- 浏览器 UA 下 `/api/health`、`/app`、`/canvas`、`/api/tools` 全 **200**。
- 裸 `curl`（默认 UA）返回 403「不支持通过 SDK Token 直接访问」是魔搭网关按 UA 判定，
  **不是死链**；评委用浏览器访问正常。对外发布链接有效。
- 线上一致性属于 G8 佐证，不改变 G4–G7 的红色结论。
