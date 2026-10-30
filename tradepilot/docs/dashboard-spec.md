# 运营大屏规格（web/index.html + web/app.js + web/style.css）

本文只描述**前端三文件**的实现口径。数据全部来自 `GET /` 背后的 WebSocket 任务流
（`/ws/mission`：`mission_start` / `decision` / `mission_done` / `error`）与
`GET /api/provenance/{id}`、`GET /api/health`。前端不新增任何后端调用假设，也不改写协议。

技术形态：**原生 `<canvas>` 2D 手绘**（无 CDN、无 Chart.js/ECharts/D3），三张图共用同一套
DPR 缩放 / `resize` 重绘 / 空数据占位基础设施（`canvasCtx` / `redrawAll` / `drawPlaceholder`）。

---

## 0. 反幻觉红线（所有渲染函数共同遵守）

1. 图上的每一个数据点都必须来自 `mission_done.report.results.<tool>.data` 中真实存在的字段；
   前端**不做估算、插值、随机抖动、示例值兜底**。
2. 字段缺失 / 为空 / 该步未执行 / 该步失败 → 画「无数据」占位，并在占位区写明原因
   （例如「本次任务未执行 opportunity_scan（mission_done 的 results 中没有该模块）」）。
3. 数值只做小数位格式化（`toFixed`），不做单位换算、不做归一化、不做二次统计；
   坐标轴范围由数据本身推导。
4. 搜索结果页（`scan_provenance_id`）与单品页（`provenance_id`）两种存证分别命名，不混用。
5. `keyword_plan` 没有搜索量数据源：界面不显示、也不推算任何搜索量。

---

## 1. 五区块 × 数据来源字段映射

### 1.1 机会雷达（`#opp-panel` + `#cv-radar`）

| 界面元素 | 数据来源字段 |
|---|---|
| 雷达 5 轴 | `results.opportunity_scan.data.candidates[].components.{yield,depth,spread,noise,terms}`（0–100 原值） |
| 每个候选一个多边形 | `candidates[]` 的顺序即绘制顺序，颜色按序取调色板 |
| 图例 | `candidates[].{keyword_used,keyword,score,rank,price_min,price_max,provenance_ids}` + `data.currency` |
| 右侧「扫描状态」 | `data.{source,currency,scans_run,method}`、`candidates.length` |
| 右侧「失败的关键词」 | `data.scans_failed[].{keyword,error}`（原样展示，不隐藏失败） |
| 脚注「口径」 | `data.basis`、分量缺失的候选名单 |
| 面板右标签 | 固定文案「canvas 2D · components 原值 0–100」 |

### 1.2 竞品价格带矩阵（`#market-panel` + `#cv-market` + `#competitors`）

| 界面元素 | 数据来源字段 |
|---|---|
| 图上方说明行 | `data.{source,keyword_used,currency,channel,robots,filtered_noise,item_url_recorded}` |
| 搜索页链接 + 搜索页存证 | `data.source_url`、`data.scan_provenance_id` |
| 散点 | `data.items[].price`（横轴）＋ 该 item 在 `items[]` 中的次序（纵轴） |
| 三条参考线 | `data.price_band.{min,median,max}` |
| 点上标注 | `data.items[].provenance_id` |
| 表格「商品 / 价格 / 来源」 | `items[].title / price / source_url`、`data.currency` |
| 表格「存证」列 | `items[].provenance_id`（单品页）+ `items[].scan_provenance_id`（搜索页） |
| 表格「抓取时间（本地）」 | 由 `GET /api/provenance/{provenance_id}` 的 `fetched_at` 回填，UTC → 本地时区 |
| 面板右上角计数 | `GET /api/health` 的 `provenance.total` |

### 1.3 定价沙盘（`#pricing-panel` + `#cv-pricing` + `#pricing`）

| 界面元素 | 数据来源字段 |
|---|---|
| 柱 | `data.sensitivity[].{margin_pct,floor_price}` |
| 柱色 | `sensitivity[].in_band` → 绿；`below_band` → 黄；其余 → 青（字段缺失则统一青色，不推断） |
| 阴影带 | `data.price_band.{min,max}` |
| 带中位虚线 | `data.price_band.median` |
| 红色虚线 | `data.floor_price` |
| 明细行 | `fx_rate`/`fx_provenance_id`、`cost_basis_cny`、`unit_cost_usd`、`cost_basis_note`、`landed_cost`、`cost_breakdown.*`、`band_source`、`band_currency_note`、`suggestion.*`、`assumptions` |

### 1.4 合规体检卡（`#compliance-panel`）

| 界面元素 | 数据来源字段 |
|---|---|
| 顶部五个数 | `live_verified`（已定位条款）/ `content_ok_sources`（内容可核验）/ `http_reachable_sources`（HTTP 可达）/ `blocked+offline` 计数 / `total` |
| 每张卡的物品名 | `checks[].item` |
| 四态徽章 | `checks[].verification` ∈ `clause_matched` / `page_only` / `blocked` / `offline` |
| 条款原文 | `checks[].excerpt`（>180 字折叠为 `<details>`） |
| 命中词 | `checks[].matched_terms[]` |
| 拦截原因 | `checks[].block_reason` |
| 来源行 | `checks[].{kind,authority,page_url,source_url,http_status}` |
| 卡上存证号 + 抓取时间 | `checks[].provenance_id` → `GET /api/provenance/{id}`；为 `null` 时显示「本页不可核验，未落存证」并留空时间 |
| 脚注 | `data.basis` |

**四态渲染是硬要求**：`blocked`（HTTP 200 但 WAF/SPA 空壳/软 404）与 `offline` 一律不得呈现为「已验证」；
`verified` 仅在 `clause_matched` 时为真，界面用四个不同颜色徽章 + 不同文案区分。

### 1.5 决策时间线（`#decision-panel`）

| 界面元素 | 数据来源字段 |
|---|---|
| 决策卡（流式） | `decision` 事件：`{n,tool,decision,attempt,reason,summary,status,gate,elapsed_s,input_keys,candidates[]}` |
| 采纳/回落徽章 | `llm_adopted` → 「采纳模型决策」；`llm_rejected` → 「模型建议越界 · 已回落规则」；两者皆假 → 「规则评分」 |
| 候选 chips | `candidates[].{name,score,eligible}`，`name === tool` 高亮为已选 |
| 重试标记 | `attempt > 1` |
| 底部汇总 | `autonomy.{steps_executed,retries,llm_decisions,llm_overrides,llm_rejected_to_rule,steps_degraded,steps_not_run}` |

口径：底部「LLM 采纳决策」= `autonomy.llm_decisions` = **规划引擎的模型选择被真正执行的步数**
（被越界拦下并回落规则的不计入）。界面不再用 `reason` 前缀之类的前端自造判据，也不新增 `llm_overrides`
之外的自创指标。

### 1.6 其余面板（保持并加固）

| 面板 | 字段 |
|---|---|
| 关键词层 | `keyword_plan.data.buckets.{core,attribute,scenario,audience,related}[].{observed,english,count,coverage,evidence}`、`competition_proxy.*`、`search_terms`、`search_terms_bytes` |
| Listing 工坊 | `listing_draft.data.{copy.title,copy.bullets[],copy.description,generated_by,facts_used}` |
| 文案审计 | `listing_audit.data.{score,grade,verdict,counts,issues[],stats,limits,marketplace,basis}` |
| 报告头 | `report.{mission_id,goal,plan_engine,llm_model,started_at,finished_at,elapsed_s,provenance.total}` |

---

## 2. 每张图的计算口径

所有坐标都由数据推导，**前端只做线性映射**：

- **雷达**：5 轴固定顺序 `yield → depth → spread → noise → terms`，角度 `-90° + i·72°`；
  半径比例 `r = R × v / 100`，其中 `v` 为后端 0–100 分值并 `clamp(0,100)`。
  网格环固定 20/40/60/80/100（因为分值域本身是 0–100 约定，不是前端推断）。
  某个候选的五维分量任一缺失 → **该候选整体不绘制**，在图例脚注里点名，不补 0。
- **竞品矩阵**：`x(price)` 线性映射到 `[price_band.min, price_band.max] ∪ [min(items.price), max(items.price)]`
  的外扩 12% 区间；若全部价格相同（退化）则用 `±max(|v|×10%, 1)` 的对称窗口，避免除零。
  `y(rank)` 按 `items[]` 返回顺序 1..n 均匀分布（n=1 时居中）。参考线为 `min/median/max` 三条竖线，
  标签横向避让防止压字。**纵轴不重排、不按价格排序**，位次即"样本第几条"。
- **定价沙盘**：柱子高度 `py(floor_price) = (h-B) - (v/hi)*(h-B-T)`，`hi = max(敏感性最大值, price_band.max, floor_price) × 1.18`；
  纵轴刻度用 1/2/5×10ⁿ 的"好看步长"（`niceStep`）；阴影带= `price_band.min..max`，
  `floor_price` 用红色虚线横贯。柱顶标数值，柱下标净利率。
- **DPR / 重绘**：`canvas.width = cssW × min(DPR,3)`，再 `ctx.setTransform(dpr,0,0,dpr,0,0)`，
  因此所有绘制代码只用 CSS 像素坐标，文字在高分屏保持锐利；`resize` 与容器 `ResizeObserver`
  触发 `redrawAll()`（宽度/DPR 变化超过阈值才重绘，避免抖动）。

---

## 3. 空数据策略（逐块）

判定顺序：`step == null`（本次未执行）→ `step.status !== "done" || !step.data`（失败/被闸门拦下）→ 正常渲染。

| 情形 | 界面表现 |
|---|---|
| 本次未执行该工具（如没跑 `opportunity_scan`） | 面板显示「本次任务未执行 opportunity_scan（报告 results 中无该模块）」；canvas 画虚线框 + 「无数据」+ 原因 |
| 工具报错（`status=error`，`data=null`） | 原样显示后端 `summary`（如「仅取得 1 条可用商品——样本不足 3 条」），表格留空，图内「无数据」 |
| 字段存在但数组为空 | 「返回的商品列表为空」/「候选为空」等，不画空坐标轴假装有图 |
| 数值字段为 `null`/非数字 | `fixed()`/`num()` 归一为「—」；`price` 全不可解析时整图占位 |
| 所有价格相同 / 敏感性只有一个点 / `hi=0` | 走 `if (!(hi > lo))` 与 `max(...,0.01)` 的退化分支，不抛异常、不出现除零 |
| `components` 缺失 | 该候选不绘制并在脚注点名 |
| 存证接口失败 | 时间列显示「存证#N 读取失败（原因）」；弹层显示「读取失败……不猜测内容」 |
| WS 未收到 `mission_done` 就断开 | 提示「任务通道已关闭，未收到 mission_done——界面保持无数据，不补造结果」 |

---

## 4. 界面元素 ↔ 存证号对应表

| 界面位置 | 存证号来源 | 点击后 |
|---|---|---|
| 竞品表「存证」列（主） | `items[].provenance_id`（kind=item，单品页 URL） | `/api/provenance/{id}` |
| 竞品表「存证」列（`/ 页#N`） | `items[].scan_provenance_id`（kind=page，搜索页） | 同上 |
| 竞品矩阵说明行「搜索页存证」 | `data.scan_provenance_id` | 同上 |
| 雷达图例后的「存证#N」 | `candidates[].provenance_ids[]`（前 4 个） | 同上 |
| 定价沙盘「现场汇率」行 | `data.fx_provenance_id`（kind=fx） | 同上 |
| 合规卡来源行 | `checks[].provenance_id`（kind=policy；`blocked`/`offline` 时为 `null`） | 同上 / 显示「本页不可核验，未落存证」 |

溯源弹层展示：`id / kind / url（可点开原始页面）/ fetched_at（本地时区 + 原始 UTC）/ content_hash /
payload 关键字段（按标题、价格、来源等优先排序，超长折叠）`。

---

## 5. 参数与协议

- 任务 payload = `MissionIn` 字段子集：`goal, keyword, keyword_cn, source, category, marketplace,
  unit_cost_cny, freight_usd, duty_pct, last_mile_usd, commission_pct, target_margin_pct`；
  其中 `commission_pct`（默认 15）与 `target_margin_pct`（默认 20）为本次补齐，默认值与后端 `MissionIn` 一致。
- `source` 下拉：`dangdang`（CNY 参考价）/ `newegg`（Newegg · 美国站 USD）/ `walmart`（部署态）。
- 事件类型与路径未改动；新增的前端网络调用只有已存在的 `GET /api/provenance/{id}`。
- 界面无 emoji；左右两栏在 ≤1180px 折为单栏，≤820px 内层再折，`overflow-x` 不产生横向滚动。
