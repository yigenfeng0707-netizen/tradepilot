# 数据源审计（sources audit）

本文件只记录**实测过**的事实。每条都带命令或抓取时间/体量；没有实测支撑的结论一律标为"未验证"。

- 审计时间：2026-10-30（Asia/Shanghai），本机出口网络（中国）
- 相关代码：`app/tools/sources.py`、`app/tools/fetch.py`、`tests/test_parsers.py`、`tests/test_competitor_filter.py`
- 红线不变：价格必须来自真实抓取，带来源 URL + 时间戳；抓不到就报错，绝不生成合成数据、绝不硬编码价格。

---

## 1. 源清单（当前注册）

| 源 | 币种 | 通道 | 实测可达性 | 状态 |
|---|---|---|---|---|
| `dangdang` 当当网 | CNY | browser | 可达（既有源，价格由前端渲染） | 可用 |
| `newegg` Newegg 美国站 | USD | browser | **可达**：公开搜索页、免登录，robots.txt 允许 | **本次新增，已实测** |
| `walmart` Walmart 美国站 | USD | browser | 不可得：robots.txt 对通用 UA 禁止 `/search` | 解析器保留，**不承诺可达** |

`newegg` 注册项：`app/tools/sources.py:31-39`

```python
"newegg": {
    "label": "Newegg · 美国站竞品价（USD，公开搜索页/免登录）",
    "search_url": "https://www.newegg.com/p/pl?d={kw}",
    "currency": "USD",
    "channel": "browser",
    "lang": "en",
}
```

> `channel` 必须是 `browser`：同一 URL 用 httpx（真实 Chrome UA）直取返回 **403 / 5766 字节**，浏览器通道返回 **1,569,500 字节**真实结果页。`fetch.py:28` 的 `BROWSER_FIRST` 也加上了 `newegg.com`，保证任何调用路径都不会先走 httpx 拿 403。

---

## 2. 真实抓取实测记录（Newegg）

### 2.1 直接调 `scan()`

命令（仓库根目录，`respect_robots` 保持默认 1）：

```powershell
cd D:\电商\tradepilot
python -c "import asyncio; from app.tools.sources import scan; r=asyncio.run(scan('newegg','usb c cable',12)); print(len(r['items']), r['currency'], r['robots']); [print(i['price'], i['title'][:60], i['source_url']) for i in r['items'][:3]]"
```

实测输出（原样）：

```
12 USD robots.txt 允许
18.99 Anker USB C to USB C Cable, Type-C 60W Fast Charging Cable ( https://www.newegg.com/p/181-00KG-000K0
9.99 Anker USB C Cable 100W 10ft, New Nylon USB C to USB C Cable  https://www.newegg.com/anker-10-feet-usb-cable-black/p/0S8-014F-00235
9.99 Anker Cable [2 Pack 6ft], USB A to USB C Cable for iPhone 17 https://www.newegg.com/anker-6-ft-usb-cable-black/p/0S8-014F-001W8
```

汇总：

| 项 | 结果 |
|---|---|
| 抓到条数 | **12 条**（`limit=12` 上限；页面内可解析 `"FinalPrice":` 字段 45 处） |
| 币种 | **USD** |
| robots 判定 | `robots.txt 允许`（`robots_allowed()` 返回 `(True, 'robots.txt 允许')`） |
| 抓取通道 | `browser`（Playwright Chromium） |
| 价格带 | **9.99 – 41.48 USD**（中位 15.99） |
| 单品页链接 | 12/12 来自结果页 DOM 中真实存在的 `<a href="…/p/…">` |

前 3 条（标题 / 价格 / 单品链接）：

1. `Anker USB C to USB C Cable, Type-C 60W Fast Charging Cable (6FT, 5Pack) for iPhone 16 Series…` — **$18.99** — https://www.newegg.com/p/181-00KG-000K0
2. `Anker USB C Cable 100W 10ft, New Nylon USB C to USB C Cable 2.0, Type C Charging…` — **$9.99** — https://www.newegg.com/anker-10-feet-usb-cable-black/p/0S8-014F-00235
3. `Anker Cable [2 Pack 6ft], USB A to USB C Cable for iPhone 17 Series, Samsung Galaxy…` — **$9.99** — https://www.newegg.com/anker-6-ft-usb-cable-black/p/0S8-014F-001W8

### 2.2 走完整工具链 `market_scan`（含书籍/跨品类/去重过滤 + 存证）

| 检索词 | 品类 | 原始 | 保留 | 剔除 | 价格带（USD） |
|---|---|---|---|---|---|
| `usb c cable` | electronics | 12 | **12** | 0 | 9.99 – 41.48 |
| `dog bed` | pet | 12 | **8** | 4 | 14.38 – 198.99 |

（`dog bed` 的 4 条剔除里包含非宠物床标题与短标题商品；保留的 8 条单品链接均落在 `/p/<item>` 上，`item_url_recorded=8`。）

### 2.3 单品链接的"真"怎么证明（诚实边界）

- **已证实**：12 条 `source_url` 的 `/p/<token>` **全部**出现在这次真实抓取的结果页 HTML 里（逐条计数 2–8 次），说明链接不是拼出来的。
- **顺带发现并修掉的坑**：旧编码商品（`Item=9SIABU5GEX2388`、`ParentItem=12-356-245`）的单品页真实链接是 `/p/N82E16812356245`；按 `ParentItem` 拼 `/p/12-356-245` 会指错。现在优先取 DOM 里的 `<a href>`，拼字符串只作最后兜底（`app/tools/sources.py:261-301`）。
- **未证实 / 如实标注**：本机**直访**单品页会被人机校验拦下（`https://www.newegg.com/p/181-00KG-000K0` → 302 到 `/areyouahuman?referer=…`，页面 `<title>Are you a human?</title>`）。因此存证口径是"搜索页 + 结果页里的单品 href"，**不声称单品页可被无头浏览器复访**。

---

## 3. 候选源不可得证据（同机同时段实测）

| 候选 | 方式 | 实测结果 | 判定 |
|---|---|---|---|
| AliExpress `www.aliexpress.com/w/wholesale-dog-bed.html` | httpx | 200 但仅 4282 字节空壳 | 受登录墙阻挡 |
| 同上 | 浏览器 | 跳转 `/p/ug-login-page/login.html?from=sm&return_url=…`，无价格、无商品卡 | **不可得** |
| Temu `www.temu.com/search_result.html?search_key=dog bed` | httpx | 200 / 2915 字节 | 受登录墙阻挡 |
| 同上 | 浏览器 | 跳转 `/login.html?from=…`（"Email or phone number / Continue with Facebook"） | **不可得** |
| eBay `www.ebay.com/sch/i.html?_nkw=dog+bed` | httpx / 浏览器 | 403；页面 "Something went wrong on our end" | **不可得** |
| Etsy `www.etsy.com/search?q=dog+bed` | 浏览器 | 403（1509 字节） | **不可得** |
| Bing Shopping `www.bing.com/shop?q=dog+bed` | 浏览器 | 被重定向到 `cn.bing.com/search?q=dog+bed`（普通网页搜索，非购物聚合），文本中 `$` 价格 0 条 | **不可得**（未降级成别的数据） |
| Target / Home Depot / Kohl's | 浏览器 | 分别返回"未找到匹配 + 出错"、403（Akamai reference）、403 | **不可得** |
| Wayfair | 浏览器 | 429 + "Press & Hold to confirm you are a human" | **不可得** |
| DHgate `www.dhgate.com/wholesale/search.do?act=search&searchkey=dog+bed` | 浏览器 | 200，文本中 **130** 处 `$xx.xx` 真实价格 | **可达但合规放弃**：robots.txt 含 `Disallow: /*?`（屏蔽带查询串的 URL）。Python `robotparser` 不实现 `*` 通配因此误判为允许，按 robots 语义应视为禁止，故**不接入** |
| LightInTheBox / Banggood | 浏览器 | 403（Akamai）/ 200 但正文无价格 | 不可得 |
| Alibaba `www.alibaba.com/trade/search?SearchText=dog+bed` | 浏览器 | 200，但正文仅 1 处 `$80`（批发询价页，非零售价带） | 不适合作竞品价源 |

> 优先级按要求先试速卖通 → Temu → Bing 购物聚合，三者全部不可得后才改用 Newegg（美国站公开搜索页）。没有把任何 mock/合成数据接进 `SOURCES`。

---

## 4. `fetch.py` 拦截页判定的改动（回归修复）

**问题（实测）**：Newegg 结果页 1,569,500 字节、含 45 处 `"FinalPrice":`，旧逻辑却判为"拦截页"，`scan()` 直接报错"该源在当前网络不可达"。原因：旧标记里有裸 `robot`，而正常页面 `<head>` 就有 `<meta name="robots">`；同时前 20k 字符里没有 `price`。

**改后**（`app/tools/fetch.py:29-35`、`:83-89`）：

```python
_CHALLENGE_MARKS = ("robot or human", "verify you are human", "are you a robot",
                    "captcha", "just a moment", "checking your browser",
                    "unusual traffic", "access denied")
_PRODUCT_SIGNALS = ("price", "product", "itemprop", "sku")

def _looks_blocked(html: str) -> bool:
    low = html[:20000].lower()
    if len(html) < 4000:
        return True
    if any(sig in low for sig in _PRODUCT_SIGNALS):   # 有真实商品字段就不是挑战页
        return False
    return any(mark in low for mark in _CHALLENGE_MARKS)
```

- Walmart 拦截页仍判为拦截：17,612 字节、含 `robot or human`/`captcha`，且前 20k 无 `price`/`product`/`sku`/`itemprop` → 返回 True（实测）。
- **误判风险边界（如实写明）**：若某个挑战页的前 20k 里恰好出现 `price`/`product`/`sku`/`itemprop`（例如内联 JS 里带 marketing 文案），会被判为"未拦截"；此时解析器解析不出商品 → `scan()` 抛"页面可达但未解析出真实商品数据"。结果仍是**如实报错**，不会输出假数据，只是错误话术从"不可达"变成"解析失败"。
- 同时删掉了 `fetch.py` 里重复定义两次的 `fetch_browser`（后者覆盖前者，属死代码），行为不变。

---

## 5. 品类相关性：改前 / 改后

### 5.1 改前（单层"包含任一 token"）

```python
CATEGORY_TOKENS = {
    "pet": ("狗", "犬", "猫", "宠物", "窝", "猫砂", "牵引", "航空箱"),
    "home": ("家居", "收纳", "挂钟", "台灯", "枕", "垫", "架", "箱", "柜"),
    "electronics": ("数据线", "充电", "手机", "耳机", "电源", "适配器", "电子"),
}
```

实测问题：`electronics` 含泛化词"充电"，于是搜 `usb c cable` 时当当返回的
`飞科(FLYCO) 男士电动剃须刀…USB Typc-C充电FS889`（¥180，见 `assets/audit_sample.json` provenance 177）被判为相关，
直接把 CN 价格带上限抬高一个量级；`home` 的"箱/架/柜"同理，任何带"箱"的商品都能混进家居样本。

### 5.2 改后（两层：主名词命中 + 排除词）

- 规则表：`app/tools/sources.py:74-116`（`CATEGORY_RULES`，每类 `primary` + `exclude`）
- 判定函数：`app/tools/sources.py:137-157`

```python
def relevant(title, category, keyword="") -> bool:
    rules = CATEGORY_RULES.get(category)
    if not rules: return True                    # 未登记品类：保持"放行"旧行为
    low = title.lower()
    if not any(t.lower() in low for t in rules["primary"]):
        return False                             # 第 1 层：必须命中主名词
    kw = keyword.strip().lower()
    kw_all = f"{kw} {CN_KEYWORD_ALIASES.get(kw, '')}".strip()
    hit_excluded = [w for w in rules["exclude"] if w.lower() in low]
    if hit_excluded:                             # 第 2 层：命中排除词则不相关
        return any(w.lower() in kw_all for w in hit_excluded)   # 除非查询本身在找它
    return True
```

主名词只收**具体实物名**：`狗床/狗窝/猫窝/宠物床/猫砂/dog bed/cat bed…`、`数据线/充电线/usb c/type-c/cable/cord…`、
`收纳箱/储物箱/挂钟/台灯/置物架/storage box…`；不再接受"充电""架""箱""柜""电子"这类泛化词。

排除词只收**整机/人用品**：`剃须刀/shaver/razor`、`充电宝/移动电源/power bank`、`音箱/显示器/笔记本/打印机`；
宠物类排除人用寝具 `宿舍/寝室/租房/单人床/双人床/榻榻米/上下铺/折叠床…`（`HUMAN_BEDDING` 仍从规则表派生，`sources.py:121`）。

行为差异（均有用例锁定，见 §6）：

| 标题（真实抓取） | 品类 / 检索词 | 改前 | 改后 |
|---|---|---|---|
| `飞科(FLYCO) 男士电动剃须刀…USB Typc-C充电FS889` | electronics / `usb c cable` | 相关（含"充电"） | **不相关**（主名词 `usb c` 命中，但排除词"剃须刀"） |
| `Type-C插头安卓手机数据线…尼龙编织USB充电线` | electronics / `usb c cable` | 相关 | **相关**（主名词 `数据线` 命中，无排除词） |
| `罗马仕 充电宝 20000毫安 Type-C 双向快充` | electronics / `usb c cable` | 相关（含"电源"） | **不相关**；但 `keyword="power bank"` 时放行（否则搜充电宝会被自己过滤空） |
| `车载后备箱整理箱 大号 塑料` | home | 相关（含"箱"） | **不相关**（无 `收纳箱/储物箱` 等主名词） |
| `线条小狗床垫…租房…宿舍寝室` | pet | 不相关（旧逻辑单列 pet 人用寝具判断） | **不相关**（两层通用机制覆盖，`HUMAN_BEDDING` 断言保留） |

**反向误杀修复**：排除词最初含英文 `monitor/laptop/printer`，实测把真实线缆
`Hannord USB C to USB C Cable 3.2 Gen 2 … 4K Monitor`（$10.99）误杀（`usb c cable` 从 12 条掉到 11 条）。
这些词常出现在线缆的"适用设备"描述里，已从排除词中移除英文版（中文整机词保留），并加用例锁定（§6）。

"宁缺勿凑"不变：过滤后 <3 条仍由 `app/tools/competitor.py:46-53` 报错，不放宽、不补数。

---

## 6. 测试

```powershell
cd D:\电商\tradepilot
python -m unittest discover -s tests -t .
```

结果：**128 个用例全绿**（本轮新增 13 个：解析器 7 个 + 品类过滤 6 个）。单测沿用 `tests/__init__.py` 的约定（临时数据目录 + `TRADEPILOT_RESPECT_ROBOTS=0`），全部离线，夹具为内联 HTML，不联网。

| 用例 | 位置 | 锁定内容 |
|---|---|---|
| `TestNeweggParser.test_parses_products_from_initial_state` | `tests/test_parsers.py:116` | `window.__initialState__` 内联 JSON 正常解析（2 条、$18.99/$17.99） |
| `TestNeweggParser.test_item_url_is_real_product_page` | `tests/test_parsers.py:123` | 单品链接取自 DOM；旧编码商品走 `/p/N82E…` 而非拼 `ParentItem` |
| `TestNeweggParser.test_missing_price_or_title_returns_empty` | `tests/test_parsers.py:133` | 缺价格/标题 → 空表（不猜价格） |
| `TestNeweggParser.test_empty_and_unknown_html_returns_empty` | `tests/test_parsers.py:136` | 空 HTML / 无 state → 空表 |
| `TestNeweggParser.test_truncated_state_falls_back_to_slice_matching` | `tests/test_parsers.py:140` | JSON 被截断时退到切片兜底 |
| `TestBlockedDetection.test_meta_robots_and_late_price_is_not_blocked` | `tests/test_parsers.py:156` | 1.5MB + `<meta name="robots">` 正常页不得判为拦截（本次误判回归） |
| `TestBlockedDetection.test_challenge_page_without_product_signals_is_blocked` | `tests/test_parsers.py:171` | 含 "Verify you are human" 且无商品信号 → 拦截 |
| `TestCategoryRelevance.test_shaver_never_enters_usb_c_cable_sample` | `tests/test_competitor_filter.py:61` | 真实剃须刀标题不得进入 `usb c cable` 样本 |
| `TestCategoryRelevance.test_power_bank_is_not_a_cable` | `tests/test_competitor_filter.py:72` | 整机排除 + 查询本身在找时放行 |
| `TestCategoryRelevance.test_cable_mentioning_device_in_specs_is_kept` | `tests/test_competitor_filter.py:79` | 反向误杀回归（`4K Monitor` 线缆必须保留） |
| `TestCategoryRelevance.test_generic_words_no_longer_qualify` | `tests/test_competitor_filter.py:47` | 泛化词"箱"不再命中家居 |
| `TestCategoryRelevance.test_real_cable_title_is_kept` | `tests/test_competitor_filter.py:55` | 真实数据线标题必须保留 |
| `TestCategoryRelevance.test_keyword_argument_is_backward_compatible` | `tests/test_competitor_filter.py:88` | `relevant()` 新签名向后兼容（两参调用 == `keyword=""`） |

---

## 7. 未覆盖 / 未验证（明确不声称）

1. **Walmart 未作为可达源**：`robots_allowed('https://www.walmart.com/search?q=dog+bed')` 实测返回
   `(False, 'robots.txt 禁止抓取 …')`；浏览器直取是 17,612 字节的 "Robot or human?" 拦截页。解析器代码保留，但**任何环境都不承诺可用**。
2. **Temu / AliExpress 无 parser**：两者都只有登录墙这一条路，本作品不绕过，故没有写解析器（写了也只能返回空表）。
3. **Newegg 单品页不可无头复访**：直访触发 `/areyouahuman`，存证以"搜索页 + 单品 href"为准（见 §2.3）。
4. **类别覆盖**：Newegg 实测覆盖 `electronics`（线缆）与 `pet`（狗床，其 marketplace 有货）；`home` 品类未在 Newegg 实测过，USD 家居竞品价本轮**没有**验证来源。
5. **限速/礼貌**：沿用 `CONFIG.per_host_min_interval_s = 3.0` 与 `TRADEPILOT_RESPECT_ROBOTS=1` 默认值；本轮实测共 2 次搜索页抓取 + 1 次 robots.txt，未做并发压测。




## 选品雷达 × 数据源可达性实测（2026-10-30）

`opportunity_scan` 用内置假设词扫 dangdang（CNY）时，5 个 pet 候选词只有 `dog bed`（→狗床）出数，
其余 4 个报「页面可达但未解析出真实商品数据」。逐页取证结果：

| 检索词 | HTTP | 页面字节 | `<li>` | search_now_price | price_n | 解析条数 | 判定 |
|---|---|---|---|---|---|---|---|
| 狗床 | 200 | 317,495 | 409 | 2 | 63 | **12** | 正常商品列表 |
| 猫窝 | 200 | 99,035 | 138 | **0** | **0** | 0 | 页面含「抱歉」，真实**无结果页** |

结论（有取证，非推测）：
- **不是反爬限流**：`猫窝` 单独冷启动第一次抓也复现同样结果；
- **不是解析器缺陷**：同页 `<li>` 与 `title="` 存在，但商品价标记 `search_now_price`/`price_n` 全为 0，
  与「抱歉」文案一致，即当当该检索词确实没有返回商品；
- 行为符合反幻觉红线：解析为空 → `scan()` 抛错 → 雷达把该词计入 `scans_failed` 并附原因，
  绝不以合成数据补位。

影响与处置：
- 内置假设词是「待验证假设」，其可抓性与数据源强相关。`electronics + newegg` 实测 5 词全出、
  4 词参与评分（Top1 wall charger 机会分 87.5，12 条样本，$12.99–75.23）；
  `pet + dangdang` 当前只有 `dog bed` 可用。
- 演示与答辩**使用 `source=newegg` + `category=electronics`**；需要在国内源上跑雷达时，
  通过任务面板「选品雷达候选词」显式传入当当确有货的检索词（`keywords` 参数，逗号分隔）。
- 未做的事（如实标注）：没有为当当补更多中文检索别名，也没有对无结果页做静默重试——
  静默重试会把「该源没有这个品类」伪装成「抓到了」。
