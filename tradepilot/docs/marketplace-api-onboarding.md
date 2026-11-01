# Shopee / Lazada 开放平台注册指引（M3 官方 API 接入前置）

> 本文件只写注册与资质路径，不含任何密钥。

---

## ⚠️ 零、先纠正一个容易搞错的前提

**官方 API 拿不到竞品市场数据。** 两个平台的所有接口都绑定在"已授权的卖家店铺"上：

| 官方 API 能给的 | 官方 API 给不了的 |
|---|---|
| 自己店铺的商品列表、标题、价格、库存、SKU | 竞品的价格、销量、评分 |
| 自己店铺的订单、售后、物流状态 | 平台级类目热销榜、价格带分布 |
| 平台类目树 + 类目属性模板（必填字段、枚举值） | 关键词搜索量 |
| 自己商品的曝光/点击（部分站点） | 任意第三方卖家的数据 |

所以官方 API **不解决 `market_scan` 的竞品情报需求**。东南亚竞品数据仍只有三条路：
住宅代理抓取（有合规风险）、第三方数据商采购、或如实标注"未覆盖"。
诚实降级优先于伪造覆盖——这与 `app/datasources/ads_report.py` 的价值观一致。

### 那为什么还要接

三个真实价值点，都属于"卖家侧操作"而非"市场情报"：

1. **文案生成能真正落地发布**——生成的 listing 可推送上线，这是生产级与演示品的分水岭；
2. **类目属性模板**——`GetCategoryAttributes` 给出每个类目的必填字段与枚举，
   文案生成不再靠猜字符数与属性名；
3. **客服 Agent 有真实上下文**——订单 API 让"买家说尺码不对"能关联到具体订单与 SKU，
   现在跑的是模拟工单。

### 已实现的协议层

`app/datasources/marketplace_auth.py`：两平台的签名串拼接、HMAC-SHA256、
token 过期判定（含 5 分钟刷新余量）、凭证缺失时返回 `None` 供上层降级。
23 条单测覆盖签名串逐字符正确性与降级行为。

**边界**：单测只能证明"拼接正确、格式合规"，**不能证明与平台服务端算出的签名一致**——
那必须用真实凭证打通一次接口。首次联调失败时第一嫌疑就是签名，先打印
`base_string` 与平台文档逐字符比对。

---

## 一、Shopee 开放平台（open.shopee.com）

**入口**：https://open.shopee.com/

### 1. 注册开发者账号
1. 打开 https://open.shopee.com/ ，点右上角 **Sign Up / Log in**；
2. 用**企业邮箱**注册（建议公司域名邮箱，个人邮箱也可但后续审核更严）；
3. 完成邮箱验证，登录进入 **Developer Console**。

### 2. 创建 App 拿 Partner 凭证
1. Console 内 **Create App**，填写应用名称、用途（选 "Product / Market Data" 相关类目）；
2. 创建成功后获得 **Partner ID** 与 **Partner Key**（即 Secret）；
3. 这两个值写进部署环境变量（`SHOPEE_PARTNER_ID` / `SHOPEE_PARTNER_KEY`），**绝不进仓库**。

### 3. 授权店铺（拿真实数据的必要条件）
- 走 OAuth：`https://partner.shopeemobile.com/api/v2/shop/auth_partner`，让卖家店铺授权后拿到 `shop_id` + `access_token`；
- 没有店铺授权只能调公开接口（如物流/支付基础信息），**拿不到商品与价格**。

### 4. 沙箱
- Console 提供 **Test Account**（沙箱店铺），可先跑通 `get_item_list` / `get_item_base_info` 再上生产。

### 5. 资质要求（实测口径）
- 开发者账号：邮箱即可注册；
- 调用商品/订单类 API：需要**已入驻的 Shopee 卖家店铺**（企业资质 + 店铺审核）；
- 跨境卖家可先入驻 Shopee 中国卖家中心（https://shopee.cn/）再回来授权。

---

## 二、Lazada 开放平台（open.lazada.com）

**入口**：https://open.lazada.com/

### 1. 注册开发者
1. 打开 https://open.lazada.com/ ，点 **Login / Sign Up**（右上角）；
2. 选择身份：
   - **For Lazada Sellers**：已有 Lazada 店铺 → 直接用卖家账号登录；
   - **For Service Providers**：ISV/服务商 → 走服务商入驻（需企业资料）；
3. 登录后进入 **App Console**（https://open.lazada.com/apps/portal）。

### 2. 创建 App 拿凭证
1. **Create App**，填写名称、回调地址（OAuth callback）；
2. 获得 **App Key** 与 **App Secret** → 环境变量 `LAZADA_APP_KEY` / `LAZADA_APP_SECRET`；
3. 沙箱环境：Console 内可申请 **Sandbox** 店铺做联调。

### 3. 授权店铺
- OAuth 授权链接：`https://auth.lazada.com/oauth/authorize?response_type=code&force_auth=true&redirect_uri=...&client_id={AppKey}`；
- 回调换 `access_token` 后调 `/product/item/get`、`/product/list` 等接口拿真实商品与价格。

### 4. 资质要求
- 服务商身份需企业营业执照；
- 卖家身份需 Lazada 店铺（企业支付宝/对公账户验证）。

---

## 三、接入到本项目的落地方式（M3）

1. 协议层已完成：`app/datasources/marketplace_auth.py`（签名 / token / 降级）；
2. 待凭证到位后新增 `app/datasources/shopee.py` / `lazada.py`：HTTP 调用 +
   限流 + 把响应映射成内部结构；
3. **数据用途按"卖家侧"定位**：类目属性 → 文案生成的字段模板；
   订单 → 客服 Agent 上下文；商品读写 → listing 发布。
   **不并入 `market_scan` 的价格带计算**——那是竞品数据，来源不同不可混用；
4. 无授权店铺时，接口返回 `unavailable`，Agent 如实标注，不降级成样本伪装实时。

## 四、密钥管理

- 本地：`.env.local`（已在 .gitignore）
- 线上：Cloud Run Secret Manager / WorkBuddy 环境变量
- 轮换：Partner/App Key 泄露立即在 Console 重置
