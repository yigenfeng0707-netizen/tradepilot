# TradePilot Global 整合架构方案（ADR）

> 状态：已确认　·　日期：2026-11-01　·　决策人：项目 owner
>
> 本文是「TradePilot 出海作战台」与「海丝出海 AI Agent」两个代码库的生产级整合方案。
> 不涉及任何参赛事项，按可交付、可运营的商业产品标准制定。

---

## 1. 背景与整合动机

两个代码库各自完整但各有一半：

| 维度 | TradePilot（`D:\电商\tradepilot`） | 海丝出海（`D:\hainan-opc\haisi-ai-agent\demo`） |
|---|---|---|
| 定位 | 出海作战台：8 工具自主编排的调研引擎 | 跨境电商全链路平台：5 Agent 服务化 |
| 数据 | **真实抓取**（httpx + Chromium 双通道）+ **SQLite 存证可逐条回访** | **模拟层**（`sources.py` 自述"模拟数据源"，`status="simulated"`，随机延迟假装请求）+ 硬编码 `market_data.py` |
| 市场 | 美国站（当当/Newegg 竞品 → US Listing） | 东南亚 5 国 × 8 语言 × 4 平台（Shopee/Lazada 等） |
| 能力 | 市场扫描 / 定价 / 合规（.gov 条款核验）/ 关键词 / Listing / 审计 / 选品雷达 | 市场洞察 / 智能选品 / 多语言内容 / 关税认证 / **客服 Agent（含情绪/纠纷）** |
| 前端 | 原生 JS + Canvas 大屏（无构建依赖） | React 18 + Vite + TS + Tailwind + recharts + zustand |
| 用户体系 | 无（单租户） | JWT + 内存用户表（重启即丢，SHA256 无盐） |
| 部署 | WorkBuddy 托管 + Cloud Run（均已上线验证） | Render/Vercel 配置齐全，未上线 |

**核心判断**：海丝的产品结构（多市场、多语言、客服）是 TradePilot 缺的；TradePilot 的数据可信度（真实抓取 + 存证 + 诚实降级）是海丝缺的。生产产品的底线是**数据必须真**，所以整合方向是：

> **以 TradePilot 的数据与信任层为核，吸收海丝的产品结构与市场纵深。**

模拟数据层必须整体下线——生产环境里"假装抓取"比没有数据更糟，一旦被用户发现即信任破产。

---

## 2. 产品定位

**TradePilot Global —— 真实数据驱动的跨境电商全链路智能体平台**

一句话：卖家输入品类与目标市场，平台给出「市场洞察 → 选品 → 定价 → 合规 → Listing → 客服」全链路的**可溯源结论**，每一个数字都能点回原始来源。

差异化（生产级视角，不吹概念）：

1. **数据可溯**：所有结论挂 provenance 记录（URL / 抓取时间 / 内容哈希 / 原文摘录），可逐条回访——竞品普遍只给结论不给来源
2. **诚实降级**：抓不到就报错，绝不编数据；降级路径显式可见（`steps_degraded` / `verification` 字段）
3. **双市场纵深**：北美（官方站条款核验）+ 东南亚（多平台多语言），共用一套引擎
4. **全链路而非单点**：对标 Jungle Scout 等单点工具，覆盖从调研到客服

---

## 3. 架构决策

### 3.1 仓库策略：在 TradePilot 仓库内演进（不新建 monorepo）

**决策**：以 `D:\电商\tradepilot` 为唯一代码库演进，海丝代码按模块迁入。

**理由**：
- TradePilot 已有完整测试体系（迁移时 294 项，现 559+）、部署脚本（deploy_clean / check_release_drift）、两个已上线环境——迁移成本最低
- 新建 monorepo 需要重建 CI/部署/测试体系，收益（目录整洁）不抵成本
- 海丝可运行代码仅 ~3100 行（agents + services），迁入成本可控；其文档/材料不迁

**目录规划**（增量）：

```
tradepilot/
├── app/
│   ├── tools/            # 既有 8 工具（引擎层，不动）
│   ├── agents/           # 【新增】从海丝迁入的产品层 Agent
│   │   ├── market_insight.py      # 改为调用 tools 层真实数据
│   │   ├── customer_service.py    # 新增能力，接 LLM + FAQ + 存证
│   │   └── base.py
│   ├── markets/          # 【新增】市场注册表（US / SEA-5国×平台）
│   └── provenance.py     # 既有存证（不动）
├── web/                  # 既有 Canvas 大屏（保留）
├── web-react/            # 【新增】海丝 React 前端迁入，逐步成为主前端
└── docs/production-integration.md   # 本文
```

**取舍**：双前端并存是过渡态，M2 结束时收敛为 React 主前端 + Canvas 大屏独立路由。

### 3.2 分层

```
┌─ 产品层（agents/）：market_insight / customer_service / …   海丝迁入，重写数据源
├─ 编排层（既有）：LLM 规划 + 规则评分 + 依赖图               TradePilot 核心
├─ 工具层（既有 8 工具）：market_scan / pricing / compliance / listing / audit / …
├─ 数据信任层（既有）：fetch 双通道 + provenance + 诚实降级    TradePilot 核心
└─ 市场配置层（新增）：markets/ 每市场声明源、币种、语言、合规库
```

规则：**产品层只准通过工具层拿数据，禁止直接读静态数据文件**。海丝原有 `app/data/*.py` 静态数据降级为"样本兜底"，且必须在响应里带 `data_quality: "sample"` 标记，前端明示。

### 3.3 市场扩展：市场注册表

新增 `app/markets/`，每个市场一份配置：

```python
MARKETS = {
  "US":  {sources: [dangdang, newegg], currency: "USD", languages: ["en"], compliance: "us_gov"},
  "SEA-ID": {sources: [shopee_id, lazada_id], currency: "IDR", languages: ["id", "en"], compliance: "sea_static"},
  ...
}
```

- 抓取源按市场装配；东南亚平台先接**官方 Open Platform API**（Shopee/Lazada 均有开放平台），不做网页爬取（反爬必败且合规风险高）
- 无官方 API 的源：如实标 `unavailable`，不出数
- 合规库分级：`us_gov`（真实条款核验，已有）vs `sea_static`（静态条款库，标注"未实时核验"）

### 3.4 数据层：SQLite → PostgreSQL（M3）

- 表：`provenance`（既有结构迁移）、`missions`、`users`、`chat_sessions`、`audit_log`
- 迁移工具：一次性导出脚本 + 双写过渡期
- 保留 SQLite 作为单机/演示模式（`TRADEPILOT_DB=sqlite:///...`）

### 3.5 用户体系：吸收海丝 JWT 骨架，安全升级

海丝现状不可用于生产：内存用户表（重启丢）、SHA256 无盐、JWT secret 有默认值。升级：

- 密码：argon2id（`argon2-cffi`）
- 用户表进 Postgres；JWT 短效 access（15min）+ refresh token
- `JWT_SECRET` 无默认值，缺失即拒绝启动（fail fast）
- 多租户：所有业务表带 `tenant_id`，查询强制过滤

### 3.6 前端：React 为生产主前端

- 迁入海丝 React 骨架（Vite + TS + Tailwind + recharts + zustand），保留其路由与设计系统
- TradePilot Canvas 大屏保留为独立路由 `/dashboard`（差异化资产，后续按需移植进 React）
- API 契约：以 FastAPI 自动生成的 OpenAPI 为准，前端生成类型（`openapi-typescript`）

### 3.7 部署

- 开发/单机：`docker compose up`（api + web + postgres）
- 生产：Cloud Run（已验证容器化）+ Cloud SQL；静态前端走 CDN
- CI：根仓库 `.github/workflows/ci.yml`（路径过滤 `tradepilot/**`）跑 pytest 全量
  + 前端 `tsc` + 构建；部署另有 `deploy-modelscope` 工作流自带 unittest 门禁

---

## 4. 里程碑

| 阶段 | 内容 | 验收标准 |
|---|---|---|
| **M1** ✅ | ① 本文档 ② `app/agents/` 迁入 market_insight 并接真实工具层 ③ 静态数据降级为 sample 且带 `data_quality` 标记 ④ 客服 Agent 迁入（接 LLM + FAQ，带存证） | 市场洞察报告里的价格/竞品来自真实抓取；无真实源时明示 sample；测试全绿 |
| **M2**（主体完成） | ① ~~React 前端接入真实 API~~ ✅ ② ~~智能选品~~ ✅ ③ ~~Listing 多语言~~ ✅ 9 语种 × 4 平台（见 4.2）④ ~~合规库合并~~ ✅（见 4.3）⑤ ~~全链路接真实接口~~ ✅ SSE 真进度（见 4.4）⑥ ~~Canvas 大屏独立路由~~ ✅ `/`→`/app`，大屏迁至 `/canvas` | 前端可完成一次全链路任务；语言/平台矩阵可用 |
| **M3**（进行中） | ① ~~Postgres 迁移~~ ✅ 迁移工具包 + **PG 16 容器真实验证通过**（见 4.11）② ~~auth 升级~~ ✅（见 4.6）③ ~~Shopee/Lazada 官方 API 适配器~~ ✅（见 4.7）④ ~~租户隔离~~ ✅（见 4.8）⑤ ~~Docker Compose + CI~~ ✅（见 4.9）⑥ ~~监控告警~~ ✅（见 4.10） | 多用户注册登录可用；官方 API 拉到真实数据（待真实凭证联调）；一条命令起全栈 |
| **M4**（进行中） | ~~计费/配额~~ ✅ 配额 4.12 / 运营后台 4.13 / 看板 4.14 / **token usage 计量 ✅** 4.15；价格表（**需运营定价决策**，非代码工作） | 可对外运营 |

### 4.1 M2 智能选品的数据口径（已落地，勿回退）

`app/agents/smart_selection.py` 把选品雷达的真实样本算成候选排序，**没有样本兜底路径**：
用样本算出的排序会误导调研优先级，比不给结果更危险。字段口径：

| 字段 | 口径 | 数据源 |
|---|---|---|
| `overall_score` | 五分量机会分，衡量样本分析价值与定位空间 | 真实抓取样本 |
| `market_size` / `growth_rate` | 显式返回「未接入」 | **无** |
| `competition_level` | 样本口径代理（噪声率 + 标题去重数） | 真实抓取样本 |
| `entry_difficulty` | 定价沙盘「报价下限 vs 价格带」对照 | 真实抓取 + 现场汇率 |
| `supply_recommendations` | 恒为空列表 + 说明 | **无**（1688/国际站需企业账号） |
| `predicted_sales` | 显式「未接入」 | **无** |

实现上踩过三个坑，均已写成回归测试（`tests/test_smart_selection.py`）：

1. **中文品类必须映射成工具层英文键**（pet/home/electronics）。传中文会让内置候选词
   取不到，且 `relevant()` 找不到规则导致**整层过滤被跳过**——噪声商品全部放行。
2. **`keywords` 参数是逗号分隔字符串，不是 list**。传 list 会被 `str()` 成
   `"['dog bed']"` 当成一个词去搜。
3. **`pricing_sandbox` 只认 item 级存证**，而选品雷达每词只记一条 observation 级存证。
   直接复用会恒报「可用商品数据点不足 2 条」，必须对 Top1 补一次 `market_scan`。

---

### 4.2 M2 内容生成的交付口径（已落地，勿回退）

`app/agents/content_generation.py` 产出多语言 × 多平台上架文案，`data_quality="generated"`。
文案不是数据，不能标 `real`；缺失规格一律 `[ADD: ...]` 占位，不编参数。

**发布面与编辑面的区别**：标题是发布面——带 `[ADD]` 的标题在平台后台过不了审核，
所以模型产物里的标题必须裁到第一个占位符之前。裁而不是删，因为模型会写
`[ADD: material] BPA-Free Construction`，删掉占位符就凭空多出一条卖家没声明的材质。
描述/卖点/社媒/广告属编辑面，允许占位符，但必须告警 + 进 `gaps`。

实现上踩过四个坑，均已写成回归测试（`tests/test_content_generation.py`）：

1. **提示词形状与读取路径必须一致**。提示词要顶层 `social_copy`/`ad_copy`，
   代码读 `marketing.social_copy`——两边对不上，模型写得再好社媒/广告也整块被判
   「未返回」而走兜底，等于白生成一半内容。现在两种形状都收。
2. **重建字典会吃掉键**。先把 `social` 重建成只含 4 个键的新字典，再读
   `social["hashtags"]`，读到的是新字典里不存在的键 → 模型标签全丢，
   只剩兜底的 `#Automatic`。必须在重建前取走。
3. **输出被截断不能整包丢弃**。reasoner 模型 `max_tokens` 同时装推理与正文，
   常在 `social_copy` 中途断掉；`_extract_json` 返回 None 就退回模板骨架，
   而标题/描述/卖点其实早写完了。现在用 `_salvage_json` 只保留**完整**的顶层字段，
   半截字符串一律不要（补全等于编内容），并明确告警「输出被截断」。
4. **默认单次尝试是延迟契约**。单次生成 60–90 秒，重试会把接口拖到 3 分钟级；
   超时后客户端拿到空响应，比拿到一份模板兜底更糟。需要重试的调用方显式传
   `retry_on_failure=True`。

---

### 4.3 M2 合规查询的口径（已落地，勿回退）

`app/agents/compliance.py`。这个 Agent 最容易被写成"看起来很专业"的样子——
一屏红黄绿的认证清单加一个"风险等级：中"。但那些条目如果没有出处，
就是在拿法律风险开玩笑。所以**两类市场的可信度分开标**：

| 市场 | 数据来源 | `data_quality` | 逐条 `verification` |
|---|---|---|---|
| 美国 | 现场抓官方页面（FDA/CPSC/FCC/OEHHA/USITC），在正文里定位条款原文片段 | `real`（有 ≥1 条定位到原文）/ `sample`（0 条） | `clause_matched` / `page_only` / `blocked` / `offline` |
| 东南亚 6 国 | 静态分级条款库（无官方 API、无可抓页面） | 恒为 `sample` | 恒为 `static_reference`，`verified=False` |

三条硬规矩：

1. **不认识的国家不猜**。没有该国条款库就报错并列出支持范围，绝不拿美国那套
   去套东南亚——那是最典型的假合规。
2. **不编税率**。HTS 与东盟税则没有公开免费 API，输出里不出现任何具体税率数字，
   前端也把原来的"关税计算器"换成了明确说明。一个凭感觉填的 12% 会让卖家算错报价。
3. **风险等级不是法律结论**。`risk_level` 的口径是"未在官方页面验证的强制要求条数"，
   `risk_basis` 里逐字写明这是资料完整度指标。

实测：美国宠物用品 54 秒，4 条中 1 条定位到原文（Prop 65，带原文摘录 + 存证），
其余如实标 `page_only`。

---

### 4.4 M2 全链路的口径（已落地，勿回退）

`app/agents/pipeline.py` 只做编排，不碰数据：每一步都调对应 Agent 的 `run()`，
返回原样放进 `state`。一旦编排层开始"顺手修一下数据"，单步页与全链路页就会开始对不上。

1. **单步失败不拖垮整条链**。四步相互独立（除文案依赖选品结果外），某步失败就记
   `failed` + 原因并继续。整条链报错退出会让用户连已成功的那几步都看不到。
2. **进度必须是真的**。用 async generator 逐步 yield，前端拿到一个事件点亮一个节点。
   前端 `setTimeout` 模拟进度会在真正卡住时继续往前跑，用户等到"全绿"才发现早就失败了。
   因此提供 `POST /api/agents/pipeline/stream`（SSE）；同步版 `/api/agents/pipeline` 供脚本用。
3. **`data_quality` 不合并**。四步分别标注，糊成一个总标记等于把 `sample` 洗成 `real`。

实测整链 226 秒（洞察 37s + 选品 21s + 文案 123s + 合规 46s），4/4 步完成。

---

### 4.5 M2 前端诚实性收口（已落地，勿回退）

两套前端并存时"谁是主入口"必须只有一个答案：`/` 307 重定向到 `/app`（React 主前端），
M1 原生 Canvas 运维大屏迁至 `/canvas`（侧边栏留外链入口，它由后端直出 HTML）。

前端还残留过一批**没有出处的数字**，与页面自己写的"每个数字都带来溯源存证"
直接矛盾，已全部清掉：

| 位置 | 之前 | 现在 |
|---|---|---|
| 首页统计卡 | 「50+ 覆盖市场」（编的，真实可抓只有 US）、「8 支持语言」（实际 9）、「5 合规国家」（实际 7） | 全部来自 `GET /api/capabilities`，按 Agent 定义现算 |
| 品类卡角标 | 写死的增长率 +23.5% 等 | 「可跑真实数据 / 需自填关键词」（按后端是否有内置检索词） |
| 侧边栏数据源 | 接口失败时回落一批假的 online 状态 | `GET /api/health` 真实字段：LLM 在线状态与模型名、存证条数、来源数、robots 遵守 |

原则：**能力声明（能做什么）可以列，但必须和 Agent 定义对得上；
业务数字（市场规模/增长率）没有真实数据源就写"未接入"，一个都不编。**
`/api/capabilities` 的 note 字段把这条原则写进了响应体。

---

### 4.6 M3 认证与多租户（已落地，勿回退）

**这是从零建，不是升级**：前端登录页一直在调 `/auth/login`，后端从来没有这个接口——
之前"注册"的用户名和**明文密码**直接落在浏览器 localStorage 里，前端还写了
"后端未启用 JWT 时降级到本地验证"的兜底。那不是认证，是把密码抄在门框上。
`app/auth.py` 落地后，该兜底已删除。

| 决策 | 取值 | 理由 |
|---|---|---|
| 密码散列 | argon2id（argon2-cffi 默认参数） | OWASP 推荐档位，不自行调低换 CPU |
| 访问令牌 | JWT HS256，30 分钟，载荷仅 sub/tenant/typ | JWT 是 base64 不是加密，邮箱/公司名不许放进去 |
| 刷新令牌 | 30 天，库中只存 sha256，**用一次换一次** | 重放撞上 revoked 记录 = 可检测令牌被盗 |
| JWT 密钥 | `TRADEPILOT_JWT_SECRET` > `data_dir/jwt_secret`(0600) 自动生成 | 重启不能让全体令牌作废；多实例必须显式配 env |
| 登录限速 | 同 IP 失败 10 次锁 5 分钟（进程内存态） | 多实例应换 Redis，但内存态远好于没有 |
| 密码下限 | 8 位（前后端一致） | 6 位纯小写离线爆破是分钟级 |
| 登录失败文案 | 邮箱不存在与密码错误**同一句** | 不帮人探测哪些邮箱注册过 |
| 存储 | SQLite `auth.db`（tenants/users/refresh_tokens） | 读写频率极低；上 PostgreSQL 时表结构原样迁走 |

注册即建租户（公司名），user 挂 `tenant_id`。**租户隔离的业务数据接入是 M3
后续工作**——先立身份层，避免一次性改动把存证/任务链路改崩。

前端接线踩过的坑（均已实测复现）：
1. **Login.tsx 写死 `/api/v1`，其他模块用 `/api`**——登录请求 404。路由统一到
   `/api/auth/*`。
2. **`authHeader` 曾对 `/auth/*` 一律不带令牌**——但 `/auth/me` 恰恰需要它，
   刷新页面永远 401。有令牌就带上，不值得为它开特例。
3. **会话恢复 effect 监听 `isAuthenticated` 会与登出竞态**——`logoutUser()` 还没
   清完 localStorage，恢复逻辑就拿旧令牌把用户重新登了回来。只在挂载时跑一次。

端到端实测（curl + Playwright）：注册建租户 → 登录 → 401 无账号枚举 →
/auth/me → 续期轮换（新令牌 ≠ 旧令牌）→ 旧令牌重放 401 → 登出撤销 →
撤销后续期 401 → 刷新页面会话恢复 → 登出后侧栏无邮箱。全程无 console error。

---

### 4.7 M3 Shopee/Lazada 开放平台 HTTP 客户端（已落地，勿回退）

`app/datasources/openapi_http.py`（通用请求层）+ `shopee.py` / `lazada.py`
（平台差异层），签名规则在 `marketplace_auth.py`。测试
`tests/test_openapi_clients.py`（14 条）用 `httpx.MockTransport` 守住协议红线。

先说清边界：**MockTransport 验证的是"我们发什么、怎么判成败"，
不能证明平台服务端接受我们的签名**——那必须用真实凭证
（Shopee Partner / Lazada Console Create App，凭证入 `.env.local`）联调一次。

两家平台的差异点（混用一次排查半天，全部写进了模块 docstring）：

| 差异 | Shopee | Lazada |
|---|---|---|
| 时间戳 | 秒 | **毫秒** |
| 签名大小写 | 小写 hex | **大写 hex** |
| 成功信封 | `{"error":"","msg":"","response":{}}`，error 非空即失败 | `{"code":"0","data":{}}`，code 非 "0" 即失败 |
| HTTP 200 | ≠ 业务成功 | ≠ 业务成功 |
| 店铺令牌 | access_token + shop_id 进签名串 | access_token 是普通签名参数 |

实现上的硬规矩（均有测试守护）：

1. **没凭证不发请求**：configured=False 返回 `not_configured`，不"发个请求试试"。
2. **店铺级接口缺令牌在发送前拦下**（`SHOP_SCOPED_PREFIXES`），省卖家一次盲猜。
3. **重试只给 GET + 429/5xx/网络异常**：POST 重发可能重复下单，一次失败就带回错误。
   退避时长用模块常量 `_RETRY_BACKOFF_S` 且默认参数传 None 运行时再读——
   默认参数在 def 时绑定，测试 patch 会失效。
4. **错误码原文保留不归并**：Shopee `error_param`、Lazada `10`（会话超时）各有
   平台文档含义，归并成统一字符串等于丢掉排查线索。
5. **令牌明文不回传**：`Token.as_dict()` 只给 platform/shop_id/expire_at/expired，
   access_token/refresh_token 不进响应体与日志。
6. **数字 `0` 宽容按成功处理**：Lazada 文档说 code 是字符串，但 JSON 出现数字 0
   没理由当失败——此宽容行为未经真实服务端验证，联调时留意。

踩过的坑：Lazada 令牌接口的字段可能在顶层也可能在 `data` 里
（`_token_from()` 两种形状都认，别赌文档）。

---

### 4.8 M3 租户隔离接入业务数据（已落地，勿回退）

**隔离对象是任务存档（missions），不是存证库**。这是设计决策不是遗漏：

- **mission 报告按租户隔离**：`save_mission()` 写入 `tenant_id`；list/get 按租户过滤。
  未认证（单机/演示模式）保持既有行为全部可见——匿名可用是产品形态，
  但**一旦带有效令牌就只看得见自己租户的任务**，隔离必须是"登录后收紧"，
  不是"登录后照旧全局可见"。
- **跨租户读取与不存在返回同一个 404**：不泄露"这份报告存在但不属于你"，
  与登录失败文案不区分邮箱存在性是同一条哲学。
- **legacy 存档不冒充归属**：`tenant_id` 引入前的旧存档无归属，登录租户看不到；
  把旧演示数据冒充成某租户的任务，比看不到更糟。
- **存证库（provenance）刻意保持全局**：它是数据信任层的全局事实库——
  同一 URL 的真实抓取对所有租户都是事实，也是防编造闸门的查询面。
  按租户切分只会让历史存证"消失"，没有任何信任收益。

`ws/mission` 通道暂无认证（Canvas 大屏是内部运维视图），其存档归属 None；
REST 端点（`/api/missions*` 三个）均已接租户。

端到端实测（uvicorn + curl，两个真实注册租户）：A 只见 A 的任务、B 只见 B 的、
A 读 B 的任务 404、未登录全部可见。

测试踩过的坑：`store.CONFIG` 是模块级全局，其他测试模块的 `save_mission`
也会写进"当前"目录——测试必须每类用全新 data_dir（setUpClass 替换、
tearDownClass 恢复），否则全集断言被外来文件打穿。

---

### 4.9 M3 Docker Compose + CI（已落地）

**修了一个真实缺口**：原 Dockerfile 只 COPY `app/` + `web/`（Canvas 大屏），
没有 `web-react/dist`——生产容器访问 `/app`（React 主前端）直接 404，
React 前端只存在于开发机。改为多阶段构建：阶段 1 Node `npm ci && npm run build`，
阶段 2 拷 `dist` 进运行时镜像。

**docker-compose.yml 刻意只有 api 一个服务**：应用数据层还是 SQLite/文件，
没有 Postgres 连接代码——现在塞一个 postgres 服务进 compose，看起来"起全栈"，
实际没有组件连它，那是装饰不是架构。M3-① 落地时一次加全。

验证状态（2026-11-01 更新）：此前"开发机 Docker 守护进程不可用、未 build
验证"的声明已过时——WSL/HNS 修复后 Docker 可用，多阶段镜像 build 成功，
容器冒烟注册/health//app//canvas 全 200；过程中抓出并修复两个干净环境 bug
（requirements 漏 PyJWT/argon2-cffi、lifespan 漏 auth.init_db），干净容器
冒烟自此列为改动后的固定验证。

CI（根仓库 `.github/workflows/ci.yml`）：backend（pytest 全量，数据目录指到
runner 临时盘）+ frontend（`npm ci` + `tsc` + `build`），路径过滤
`tradepilot/**`。**为什么在根不在 tradepilot/ 子树**：GitHub 只认仓库根的
`.github/workflows`，本仓库根是工作区（origin 即 GitHub），首版放在子树里
永远不会被触发，2026-11-01 移到根。测试里有真实网络抓取，GitHub runner 有
外网；若将来因目标站反爬在 CI 上抖动，先解决抖动再考虑重试策略——把抖动
藏进重试等于掩盖真实问题。

---

### 4.10 M3 监控告警（已落地，勿回退）

`app/monitoring.py` + `GET /api/metrics`。定位：**单实例的最小生产级监控**，
指标存进程内存（重启清零，如实声明）；多实例聚合属 M4（Prometheus/Cloud
Monitoring）。`/api/health` 回答"活着没有"，`/api/metrics` 回答"活着得怎么样"：
请求量、5xx 率、每 route 延迟 p50/p95、进程 RSS、告警布尔、存储层状态。

三条硬规矩：

1. **路径参数归一成 route 模板**：每个 mission_id 一个计数桶的话桶数无限
   膨胀，聚合毫无价值。`/api/missions/{id}` 永远算一个 route。
2. **只有 5xx 算错误**：404 是调用方的错，混进错误率会把"系统坏了"和
   "用户用错了"搅在一起。
3. **告警判定有样本下限**：总请求 ≥ 20 且 5xx 占比 > 5% 才报
   `high_error_rate`——三条请求错一条就报警的系统只会教人忽略告警。
   `llm_offline` / `storage_unavailable` 无条件上报。

指标中间件不记 `/api/metrics` 自身——看一次指标就把指标改了，采样器
不能污染被采样对象。RSS 只在 Linux（生产目标）读 `/proc`，其他平台如实
null，不猜。

---

### 4.11 M3 Postgres 迁移（已全链路真实验证 ✅，2026-11-01）

开发机原本没有 Postgres 服务端，工具包交付时数据侧闭环验证、PG 方言
部分留待真实环境——现已用 `postgres:16` 官方容器跑通全链路：

1. `scripts/pg_schema.sql`：DDL，表结构从 SQLite **原样迁走**（迁移是搬运
   不是重构）。仅两处类型判断：REAL→DOUBLE PRECISION；INTEGER 0/1→SMALLINT
   直通（换 BOOLEAN 就要在导出脚本里转换，转换代码是新的出错面）。
   `provenance.id` 用 `GENERATED BY DEFAULT AS IDENTITY`（允许显式插 id）。
2. `scripts/export_sqlite_to_pg.py`：**只读打开**源库导出全部表为
   INSERT 语句，末尾 `setval` 把 provenance 序列拨到 MAX(id)——没有这步，
   新写入撞主键。自检：生成的 INSERT 重新装载进内存 SQLite 库，与源库
   **逐行逐列比对**。
3. **真实环境验证结果（docker postgres:16 容器，psycopg2 执行）**：
   DDL 4 张表建表成功；真实库导出 159 行（tenants 18 / users 18 /
   refresh_tokens 20 / provenance 103）装载后逐表行数一致；
   setval 正确——MAX(id)=103，迁移后插入新行得 id=104 不撞主键；
   中文（UTF-8）写入读回正常。
4. **补充（2026-11-01 晚）**：quota_usage / llm_usage / shop_authorizations
   三张表 DDL 已补进 `pg_schema.sql` 并在同一容器验证——建表成功，
   用模块真实 upsert 语句实测：累计扣重（2.5+1.0=3.5）、token 累加、
   授权幂等（重复写只 1 行且时间刷新）、中文往返全部正确。
   **方言差异（实测踩到）**：SQLite 的 `DO UPDATE SET weight = weight +
   excluded.weight` 在 PG 报 ambiguous——裸列名须改为表名限定
   `quota_usage.weight`。即换 Postgres 时 quota/usage 两模块的
   ON CONFLICT 语句除占位符（?→%s）外还要补表名限定，两种写法已
   实测等价；注释与正确写法见 `pg_schema.sql` 配额节。

踩过的坑：`CONFIG.db_file()` 会把首次解析的库路径**缓存进实例**，
`dataclasses.replace(CONFIG, data_dir=tmp)` 只换 data_dir 的话，provenance
照样写"原来的"库——替换时必须连 `db_path=""` 一起重置。

---

### 4.12 M4 租户 LLM 配额（已落地，勿回退）

`app/quota.py` + 中间件接线。计费的第一块：先把"用量"变成可计量、可拦截的，
价格才是下一步。三条口径（都写死在代码注释里）：

1. **按请求权重扣，不数内部 LLM 调用次数**。一次全链路内部调多次 LLM，
   但商业单位是"一次任务"——权重表（pipeline=4、文案=2、其余=1）让用户
   看到的余额与账单口径一致。按 token 计费等 `llm.chat` 返回 usage 再演进，
   不提前猜。新端点上线必须在权重表表态——不进表 = 不扣 = 运营看不见的
   免费午餐。
2. **扣减前置 + 没进业务回滚**。先扣后跑（防并发刷），但 404/405/422
   （路由不存在/方法不对/参数校验失败）回滚——接口都没调对，LLM 成本为零，
   扣了就是抢钱；进了 handler 的失败不回滚（成本已真实发生）。
   超限 429 不扣不退。
3. **只约束登录租户**。匿名请求没有租户概念，扣在谁头上？匿名继续走
   运维侧全局控制。limit=0 = 配额关闭，响应里 `limited:false` 明示，
   不装作有隔离。

默认上限 `TRADEPILOT_TENANT_DAILY_LLM_LIMIT`（默认 100/天/租户）——这是
**商业参数**不是技术常量，运营改 env 即可。天边界 UTC（与 auth 一致）；
存储 `quota.db`，上 Postgres 时随认证表一起迁。

端到端实测（限额 6）：422 参数错误回滚（账本 0.0）→ 真实合规请求 200
（账本 1.0，响应头 `X-Quota-Remaining: 5.0`）。

---

### 4.13 M4 运营后台（已落地，勿回退）

`GET /api/admin/overview` + React `/app/admin` 页。第一个"平台方"视角的
端点，三条规矩：

1. **fail closed**：`TRADEPILOT_ADMIN_TOKEN` 未配置 = 后台整体关闭（403
   并明说"未启用"）。没有默认令牌，没有"先用着后补"——未配置的后台
   不是功能，是漏洞。
2. **运营者 ≠ 租户成员**：鉴权走独立 env 令牌（`X-Admin-Token`，
   `secrets.compare_digest` 防时序），不挂在租户 JWT 的 role 上——
   租户令牌访问管理端点照样 401，两套身份互不通用。单实例阶段 env
   静态令牌够用；多租户 SaaS 化时应换独立管理员账号体系。
3. **导出无敏感材料**：租户/用户清单不含密码散列与令牌哈希
   （auth.export_all 红线延续到聚合响应）；邮箱/公司名可见——运营者
   本来就有权知道谁在用平台。

前端：令牌只存 sessionStorage（标签页关了即清），绝不进 localStorage——
平台级凭证比用户会话贵重。失效令牌自动清除不反复撞。

端到端实测（Playwright）：未配置 403 → 错令牌 401 → 对令牌 200；
页面统计卡与后端一致（14 租户/配额消耗 1/存证 103），无 console error。

---

### 4.14 M4 数据看板（已落地，勿回退）

`GET /api/admin/trends` + Admin 页两张 recharts 柱状图 + 存证分布。
**只画真实可算的东西**，这是看板最大的红线：

- **任务量**：按报告 `started_at` 日期前缀聚合。started_at 坏了的任务
  不计入也不猜日期——宁可少算不可编。没任务的天**补 0**：图表上的平线
  必须是真的没任务，不是缺数据被裁掉。
- **配额消耗**：按天聚合，历史自配额功能上线起才有——之前的 days 天
  如实为 0，不回填、不估算。上线当天图表从零开始是诚实，不是难看。
- **存证分布**：按 kind GROUP BY，空库返回空字典。
- 多实例部署时任务量只是"本实例磁盘"口径，看板文案已注明；云上
  持久化后天然齐全。

注意：看板低频访问（运营者手动刷新），missions_by_day 全量扫文件
（2000 封顶）可接受；高频调用前应改为写入时聚合计数。

端到端实测：API 返回配额今天 1.0 / 其余天 0（真实）、存证 5 类分布；
页面两图 28 根柱子渲染正常，无 console error。

---

### 4.15 M4 token usage 计量（已落地，勿回退）

`app/usage.py` + `llm.chat` 记账。定价的前置：先把每次调用的真实 token
记下来，价格才是拿数字填空。

- **与配额的分工**：配额管"拦"（按请求权重，商业单位），usage 管"量"
  （按 token，成本单位）。两套口径不合并——一个是界面语言，一个是成本
  语言，混在一起两头都说不清。
- **归属走 ContextVar**（`request_context.py`）：middleware 设置一次，
  `llm.chat` 任意深度可读——**agents/pipeline 的签名一行没改**。
  给整条链加 tenant_id 参数的话，漏传一层记账就静默失真。
- **匿名不是免记录**：未登录/ws 调用记入显式 `anonymous` 键——成本真实
  发生，只是无归属。
- **缺 usage 字段如实记 0**：calls 仍 +1（成本事件不因响应缺字段消失）；
  负 token 钳 0；记账失败不影响业务返回（计量层永远不能拖垮产品）。

端到端实测（真实网关）：一次客服对话后账本记录该租户 1 次调用、
prompt 86 / completion 650 tokens；`/api/metrics` 与 admin overview
均可见今日汇总。定价表本身是运营决策，不是代码工作——数字齐了，价格
由运营填。

**用户侧 UI**（`/account` 我的用量页）：配额进度条（关闭时如实写
"未启用配额"）、今日 LLM 调用与 token、近 7 天柱状图——后端 by_day
只有有账目的天，前端按天补零（平线 = 真的没用，不是缺数据），页脚
写明配额权重/token 两套口径不互相换算。

### 4.16 M4 店铺授权归属（已落地，勿回退）

`app/shops.py`。授权码换 token 的成功只解决"能用"，还必须回答"是谁的"
——否则 A 租户授权的店铺，B 租户的请求也能用。

- **关系显式落库**：`shop_authorizations` 表，PK(tenant_id, platform,
  shop_id)，重复授权 ON CONFLICT 刷新。与 `mauth.TOKENS`（进程内 token
  缓存）刻意分离——缓存管生命周期，归属管权限，混一张表两头语义打架。
- **exchange 成功即落归属**：`/api/integrations/{platform}/exchange`
  成功后 `shops.authorize()`；shop_id 取自平台响应，取不到就存空串，
  **不猜字段**（Lazada 响应里 shop_id 的具体形态待真实联调确认）。
- **清单只列自己的**：`/api/integrations/{platform}/shops` 只返回本租户
  授权；`token_alive` 如实反映缓存令牌是否过期，不把"关系在"伪装成
  "令牌活"。
- **店铺级调用网关**：`POST .../shops/{shop_id}/call`，闸门分层——
  未登录 401 → 平台不支持 404 → 无授权关系 404（跨租户与不存在同响应）
  → 令牌不可用 409（"重新授权"的信号，不冒充 404 也不假装能用）→
  path 白名单 403（auth 类接口不得穿过网关）。平台业务错误如实透传
  502 + error_code。
- **测试 +4**：只列自己的、token_alive 诚实、新租户空列表、匿名 401。
  调用网关 +10（闸门逐层、过期 token、白名单、错误透传、明文不出响应）。
  全量 548 passed。
- **前端店铺集成页**（`/integrations`）：三步流程（生成授权链接 → code
  换令牌 → 清单+调用测试台）。诚实性对齐：token 失效显示"需重新授权"，
  Lazada 空 shop_id 显示"待联调确认"，平台错误码 `[code]` 前缀原样透传，
  页脚边界说明（令牌进程内缓存、业务接口未经真实服务端验证）。
  端到端冒烟通过（SPA 200 / 401 / 409 / 空清单 / 404）。

### 4.17 Shopify 集成（低门槛平台，真实联调通道）

Shopee/Lazada 入驻需要企业资质与真实店铺——短期内拿不到凭证，链路验证
一直悬着。**Shopify Partner development store 免费、即开即用**，选它作为
真实服务端联调通道（2026-11-01 接入）：

- **适配器** `app/datasources/shopify.py`：自定义 App token 直连模式
  （店铺后台 Develop apps → Install 得到 `shpat_` token，无需 OAuth 回调
  ——比模拟一条用不上的重定向诚实）。`verify_and_connect` 真实调
  `/shop.json`，**验证通过才落归属**——"连上"是真的能调 API。
- **端点**：`POST /api/integrations/shopify/connect`（贴 token 直连）；
  auth-url / exchange 对 shopify 返回 409 并指引正确路径（平台路径不同
  就明说，不假装统一）。shops / shops/{id}/call 完全复用现有闸门链。
- **信封形态第三种**：Shopify 是 HTTP 2xx=成功 / 4xx body={"errors"}，
  与 Shopee（200+error）/ Lazada（200+code）构成三种形态，各自独立测试。
- **线上真实验证**：connect 对不存在店铺名返回 Shopify 真实 404
  （域名/API 路径/认证头格式全部正确）——只差真实 token 即全链路通。
- **通用化**：`openapi_http.request_json` 增加 headers 参数
  （与签名无关的机械部分下沉公共层）。
- 测试 +10（test_shopify.py），全量 559 passed。

#### 4.17.1 真实联调 runbook（拿到 token 后一条命令验证）

浏览器自动化在本机不稳定（Cloudflare Turnstile 需本人点击、调试端口活不过
单次命令），因此**取 token 这步由店主在普通浏览器手动完成**，其余全链路用
`scripts/verify_shopify.py` 一次性跑通（进程内直调适配器，真实 httpx 打
Shopify，不依赖服务进程常驻）。

**第一步：手动取 token（约 3 分钟，普通浏览器）**

1. 打开 `https://partners.shopify.com` → 登录你的 Partner 账号（org 5295840）。
2. Partners 后台 → **Stores** → **Add store** → **Create development store**
   → 选 **Build a store for testing** → 填店名 → 保存。
3. 进入新店 → 左下角 **Settings → Apps and sales channels → Develop apps**
   → 若首次进入先点 **Allow development apps**。
4. **Create an app** → 填 App 名（如 `tradepilot-test`）→ 创建。
5. **Configure Admin API scopes** → 只勾这三个只读权限：
   `read_shop`（部分版本在 Configuration 页，无则跳过）、`read_products`、
   `read_orders` → **Save**。
6. **API credentials** 页 → **Install app** → 成功后复制
   **Admin API access token**（`shpat_` 开头，只显示一次）。
7. 店铺域名：地址栏 `https://<店名>.myshopify.com/admin` 中的 `<店名>.myshopify.com`。

**第二步：写凭证（不进仓库/聊天）**

在 `tradepilot/.env.local`（已在 .gitignore）写两行：

```
SHOPIFY_SHOP=你的店名.myshopify.com
SHOPIFY_TOKEN=shpat_你的token
```

**第三步：一条命令跑全链路**

```bash
cd D:/电商/tradepilot
/d/miniconda3/python.exe scripts/verify_shopify.py
```

脚本依次验证：① connect 真实调 `/shop.json` → ② 授权关系落库 →
③ 店铺级调用读真实商品 → ④ 读真实订单，输出逐项通过/失败报告。
哪步失败会给出针对性排查提示（域名拼写 / scope 是否够 / 是否 Install 后复制）。
脚本只读不写，token 仅本机内存使用，不外发、不回显（只打印掩码）。

**已就绪的代码侧**：适配器、端点、归属闸门、前端集成页、13 条联调/适配器单测 +
全量 583 passed + 脚本编排逻辑（fake transport，已入库为 `tests/test_verify_shopify.py`）
均已验证；唯一缺口就是真实 token 这一步。成功后脚本会写脱敏结论到
`assets/shopify_verify_result.json`，生产门禁据此判定 G5。拿到 token 后若脚本报错，
把**错误码和排查提示**（不要贴 token）发我，我据此定位。

### 4.18 生产就绪门禁与密钥退役（2026-11-20）

`scripts/production_gate.py` 把交付验收拆成 8 项（G1 测试/构建、G2 CI、G3 分支同源、
G4 无明文凭证、G5 真实平台联调、G6 人工评测实数、G7 定价决策、G8 文档无漂移）。
可自动采集的字段由 `collect` 刷新；测试/CI 只认证测值，缺证据即 FAIL：
**门禁不会把"没跑"解释成"通过"，也不会替人填人工基线或抽检结果。**

明文密钥退役顺序（不可颠倒）：先在网关吊销 → 再跑
`python scripts/retire_keys.py --revoked --apply` 删除本地实体。
脚本默认只检查，没有 `--revoked` 时连 `--apply` 都拒绝执行；
日志只记文件名、字节数、sha256 前 12 位，不含密钥内容。
详细 runbook 见 `docs/ops-readiness.md` §5、§6。

---

## 5. 风险与对策

| 风险 | 影响 | 对策 |
|---|---|---|
| 东南亚平台无公开网页可爬 | 市场洞察无真实数据 | 走官方 Open Platform API；无 API 的源明示不可用 |
| 模拟数据残留被误当真实 | 信任崩塌 | `data_quality` 字段强制标注 + 前端展示 + 测试守护 |
| 双前端并存拖慢迭代 | 维护成本 | M2 收敛为 React 主前端 |
| Postgres 迁移数据丢失 | 存证断档 | 迁移前全量导出 + 校验条数与哈希 |
| LLM 成本失控 | 费用 | 保留海丝每日限额机制（`LLM_DAILY_LIMIT`）+ 按租户配额 |

---

## 6. 明确的非目标

- 不做网页爬取绕过反爬（合规风险）；只做公开页 + 官方 API
- 不做"数据猜补"：任何缺失都显式标注，不插值、不编造
- 不在 M1-M3 做移动端 App
