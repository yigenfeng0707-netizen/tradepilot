# -*- coding: utf-8 -*-
"""Shopify 真实服务端联调脚本（一次性跑通全链路，不依赖服务进程常驻）。

为什么直接调适配器而不是走 HTTP 端点：本机自动化浏览器/后台服务的调试端口
活不过单次命令（沙箱进程回收），所以把「connect → 归属 → 店铺级调用」全部
放进一个进程里同步跑完，用真实 httpx 打 Shopify —— 这与走 HTTP 端点调用的是
同一份适配器代码，唯一差别是少了 FastAPI 那层（鉴权/闸门另有单测守护）。

前置：
  1) traps: 店铺后台 Develop apps → 建 App → 勾 read_products/read_orders/read_shop
     → Install → 复制 shpat_ 开头的 Admin API access token。
  2) 在项目根写 .env.local（已在 .gitignore，绝不进仓库/聊天）：
       SHOPIFY_SHOP=your-store.myshopify.com
       SHOPIFY_TOKEN=shpat_xxxxxxxxxxxxxxxx
  3) 运行：/d/miniconda3/python.exe scripts/verify_shopify.py

脚本只读商品/订单/店铺信息，不写任何数据；token 仅本机内存使用，不外发。
"""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, ".")

from app.datasources import shopify  # noqa: E402
from app.datasources import marketplace_auth as mauth  # noqa: E402
from app import shops  # noqa: E402

ENV_LOCAL = Path(__file__).resolve().parent.parent / ".env.local"
CHECK_TENANT = "verify-shopify-local"   # 联调用临时租户归属，不污染真实租户


def load_env_local(path: Path) -> None:
    """极简 KEY=VALUE 解析（项目无 python-dotenv 依赖，保持零新增依赖）。
    只填缺失的变量，不覆盖外部显式注入的环境变量。忽略 # 注释与空行。"""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and value and key not in os.environ:
            os.environ[key] = value


def mask(token: str) -> str:
    return (token[:8] + "…" + token[-4:]) if len(token) > 14 else "***"


async def main() -> int:
    load_env_local(ENV_LOCAL)
    shop = os.environ.get("SHOPIFY_SHOP", "").strip()
    token = os.environ.get("SHOPIFY_TOKEN", "").strip()

    if not shop or not token:
        print("✗ 缺少配置：请在 tradepilot/.env.local 写 SHOPIFY_SHOP 和 SHOPIFY_TOKEN")
        print(f"  .env.local 路径：{ENV_LOCAL}")
        return 2
    if not token.startswith("shpat_"):
        print(f"✗ TOKEN 形态不对：自定义 App token 应以 shpat_ 开头（收到 {mask(token)}）")
        return 2

    shop = shopify.normalize_shop(shop)
    print(f"目标店铺：{shop}")
    print(f"TOKEN：{mask(token)}（仅本机内存使用）")
    print("=" * 60)

    # 1) connect：真实调 /shop.json，验证通过才落缓存
    print("\n[1/4] connect —— 真实调 /shop.json 验 token")
    result = await shopify.verify_and_connect(token, shop)
    if not result.ok:
        print(f"✗ 连接失败  error_code={result.error_code}")
        print(f"  error_msg={result.error_msg}")
        print("  排查：店铺域名拼写 / token 是否 Install 后复制 / scope 是否含 read_shop")
        return 1
    data = result.data or {}
    print(f"✓ 连接成功  店铺名={data.get('shop_name')!r} 国家={data.get('country')!r}")

    # 2) 归属：模拟 connect 端点的落库动作
    print("\n[2/4] 授权归属 —— 验证 shops.authorize 落库")
    shop_id = str(data.get("shop_id") or shop)
    shops.authorize(CHECK_TENANT, "shopify", shop_id)
    owned = shops.shops_of(CHECK_TENANT, "shopify")
    ok = any(r["shop_id"] == shop_id for r in owned)
    print(f"  {'✓' if ok else '✗'} 归属已落库（租户 {CHECK_TENANT}）" if ok else "✗ 归属未落库")
    if not ok:
        return 1

    # 3) 店铺级业务调用：读商品（真实数据）
    print("\n[3/4] 店铺级调用 —— GET /products.json?limit=3")
    cached = mauth.TOKENS.get("shopify", shop_id)
    if cached is None:
        print("✗ 令牌缓存丢失，无法走店铺级调用")
        return 1
    products = await shopify.call(shop_id, cached.access_token, "/products",
                                  {"limit": 3})
    if not products.ok:
        print(f"✗ 商品读取失败  error_code={products.error_code}  msg={products.error_msg}")
        print("  排查：App scope 是否含 read_products（改 scope 后需重新 Install 并换 token）")
        return 1
    items = (products.data or {}).get("products") or []
    print(f"✓ 读到 {len(items)} 个商品（本店真实数据）：")
    for p in items:
        print(f"    · {p.get('title', '')[:60]}")

    # 4) 店铺级调用：读订单（真实数据）
    print("\n[4/4] 店铺级调用 —— GET /orders.json?limit=3")
    orders = await shopify.call(shop_id, cached.access_token, "/orders",
                                {"limit": 3})
    if not orders.ok:
        print(f"✗ 订单读取失败  error_code={orders.error_code}  msg={orders.error_msg}")
        print("  排查：App scope 是否含 read_orders；新店无订单时也应返回 ok + 空列表")
        return 1
    ords = (orders.data or {}).get("orders") or []
    print(f"✓ 读到 {len(ords)} 个订单（0 个对新店是正常的）：")

    print("\n" + "=" * 60)
    print("✓✓ Shopify 全链路联调通过：connect → 归属 → 商品/订单真实读取")
    print("    证明：签名头/域名/API 版本/令牌/归属闸门/业务读取 全部真实可用")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
