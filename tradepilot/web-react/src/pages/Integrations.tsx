import { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { motion } from 'framer-motion';
import { Plug, RefreshCw, Store, ExternalLink, Play, AlertTriangle, KeyRound } from 'lucide-react';
import {
  fetchIntegrationAuthUrl, fetchIntegrationShops, exchangeIntegrationCode,
  shopApiCall, connectShopify, type ShopAuthorization,
} from '@/utils/api';
import { useStore } from '@/store/useStore';

/**
 * 店铺集成（M4）：店铺授权 + 店铺级业务调用。
 *
 * 诚实性约束（与后端口径一一对应，改任何一边都要对齐另一边）：
 * - token 状态是**进程内缓存**口径：服务重启后即失效，页面如实显示
 *   "需重新授权"，不把"授权关系在"伪装成"令牌能用"；
 * - Lazada 授权响应的 shop_id 形态待真实联调确认（可能为空占位）——
 *   空就显示"待联调确认"，不编一个 shop_id 出来；
 * - 店铺级调用走后端白名单网关（403 = 不在白名单），错误码原样透传；
 * - Shopify 走"自定义 App token 直连"（免费 development store 即可真实
 *   联调），不走授权链接——两种连接方式在页面上分开呈现，不混着说。
 */

const cardCls = 'rounded-xl border border-white/10 bg-ocean-800/60 p-5';
const inputCls =
  'w-full rounded-lg border border-white/10 bg-ocean-800 px-4 py-2.5 text-sm ' +
  'text-white placeholder-gray-500 outline-none transition focus:border-jade-500 ' +
  'focus:ring-1 focus:ring-jade-500/30';

const PLATFORMS = [
  { code: 'shopee', label: 'Shopee', mode: 'oauth' as const,
    codeLabel: '授权 code', extraLabel: 'main_merchant_id（多 merchant 场景，可空）' },
  { code: 'lazada', label: 'Lazada', mode: 'oauth' as const,
    codeLabel: '授权 code', extraLabel: 'redirect / uuid（防重放，可空）' },
  { code: 'shopify', label: 'Shopify', mode: 'token' as const,
    codeLabel: '', extraLabel: '' },
] as const;

export default function Integrations() {
  const { isAuthenticated } = useStore();
  const navigate = useNavigate();

  const [platform, setPlatform] = useState<'shopee' | 'lazada' | 'shopify'>('shopify');
  const [authUrl, setAuthUrl] = useState('');
  const [authUrlError, setAuthUrlError] = useState('');
  const [shops, setShops] = useState<ShopAuthorization[] | null>(null);
  const [shopsError, setShopsError] = useState('');
  const [loadingShops, setLoadingShops] = useState(false);

  const [code, setCode] = useState('');
  const [extra, setExtra] = useState('');
  const [exchangeMsg, setExchangeMsg] = useState('');
  const [exchangeError, setExchangeError] = useState('');
  const [exchanging, setExchanging] = useState(false);

  // Shopify 直连
  const [shShop, setShShop] = useState('');
  const [shToken, setShToken] = useState('');
  const [shMsg, setShMsg] = useState('');
  const [shError, setShError] = useState('');
  const [connecting, setConnecting] = useState(false);

  const [callPath, setCallPath] = useState('');
  const [callParams, setCallParams] = useState('{}');
  const [callMethod, setCallMethod] = useState<'GET' | 'POST'>('GET');
  const [callResult, setCallResult] = useState('');
  const [callError, setCallError] = useState('');
  const [calling, setCalling] = useState(false);

  const loadShops = useCallback(async (p: 'shopee' | 'lazada' | 'shopify') => {
    setLoadingShops(true);
    setShopsError('');
    try {
      setShops(await fetchIntegrationShops(p));
    } catch (err) {
      setShops(null);            // 失败如实留空，不拿旧平台的数据硬撑
      setShopsError(err instanceof Error ? err.message : '加载失败');
    } finally {
      setLoadingShops(false);
    }
  }, []);

  useEffect(() => {
    if (!isAuthenticated) return;
    // 切平台就重拉：两个平台的授权与令牌互不相干
    setAuthUrl(''); setAuthUrlError('');
    setShops(null); setShopsError('');
    setExchangeMsg(''); setExchangeError('');
    setShMsg(''); setShError('');
    setCallResult(''); setCallError('');
    void loadShops(platform);
  }, [platform, isAuthenticated, loadShops]);

  if (!isAuthenticated) {
    return (
      <div className="mx-auto max-w-md pt-20">
        <div className={cardCls}>
          <div className="flex items-center gap-2 text-white">
            <Plug className="h-5 w-5 text-jade-400" />
            <h1 className="text-lg font-semibold">店铺集成</h1>
          </div>
          <p className="mt-3 text-sm leading-relaxed text-gray-400">
            店铺授权是租户动作：授权后的店铺 API 调用消耗你所在租户的配额，
            店铺清单也只对授权租户可见。
          </p>
          <button
            onClick={() => navigate('/login')}
            className="mt-4 w-full rounded-lg bg-jade-600 px-4 py-2.5 text-sm font-medium text-white transition hover:bg-jade-500"
          >
            先登录
          </button>
        </div>
      </div>
    );
  }

  const current = PLATFORMS.find((p) => p.code === platform)!;

  const getAuthUrl = async () => {
    setAuthUrlError(''); setAuthUrl('');
    try {
      setAuthUrl(await fetchIntegrationAuthUrl(platform));
    } catch (err) {
      // 409（凭证未配置）等错误原样转述——运营者该知道去哪补条件
      setAuthUrlError(err instanceof Error ? err.message : '获取失败');
    }
  };

  const doExchange = async () => {
    setExchangeError(''); setExchangeMsg('');
    setExchanging(true);
    try {
      const r = await exchangeIntegrationCode(platform, {
        code: code.trim(),
        main_merchant_id: platform === 'shopee' ? extra.trim() : undefined,
        redirect: platform === 'lazada' ? extra.trim() : undefined,
      });
      setCode('');
      setExtra('');
      setExchangeMsg(
        r.shop_id
          ? `授权成功，店铺 ${r.shop_id} 已归属当前租户${r.has_refresh ? '（含刷新令牌）' : ''}`
          : '授权成功，已归属当前租户（shop_id 待联调确认）');
      await loadShops(platform);
    } catch (err) {
      // 平台业务错误（502）原样透传 error_code——排查表见 docs/ops-readiness.md
      setExchangeError(err instanceof Error ? err.message : '换令牌失败');
    } finally {
      setExchanging(false);
    }
  };

  const doConnect = async () => {
    setShError(''); setShMsg('');
    setConnecting(true);
    try {
      const r = await connectShopify({ shop: shShop.trim(), token: shToken.trim() });
      setShToken('');
      setShMsg(`已连接 ${r.shop_name || r.shop_id}——连接关系已归属当前租户。${r.note || ''}`);
      await loadShops(platform);
    } catch (err) {
      // 502 = 平台侧校验失败（token 无效/域名不通），原样转述
      setShError(err instanceof Error ? err.message : '连接失败');
    } finally {
      setConnecting(false);
    }
  };

  const doCall = async () => {
    setCallError(''); setCallResult('');
    let params: Record<string, string> = {};
    try {
      const parsed = JSON.parse(callParams || '{}') as unknown;
      if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) {
        throw new Error('params 必须是 JSON 对象');
      }
      params = Object.fromEntries(
        Object.entries(parsed as Record<string, unknown>).map(([k, v]) => [k, String(v)]),
      );
    } catch (err) {
      setCallError(err instanceof Error ? `params 解析失败：${err.message}` : 'params 解析失败');
      return;
    }
    if (!shops?.some((s) => s.token_alive)) {
      setCallError('当前没有令牌可用的店铺——先完成授权（服务重启后令牌会失效，需重新授权）');
      return;
    }
    const target = shops.find((s) => s.token_alive)!;
    setCalling(true);
    try {
      const r = await shopApiCall(platform, target.shop_id,
        { path: callPath.trim(), params, method: callMethod });
      setCallResult(JSON.stringify(r.data, null, 2));
    } catch (err) {
      // 403 白名单 / 409 重新授权 / 502 平台错误码——全部原样转述
      setCallError(err instanceof Error ? err.message : '调用失败');
    } finally {
      setCalling(false);
    }
  };

  return (
    <div className="mx-auto max-w-4xl space-y-6">
      {/* 平台选择 */}
      <div className="flex items-center gap-3">
        <h1 className="text-xl font-semibold text-white">店铺集成</h1>
        <div className="flex gap-1 rounded-lg border border-white/10 bg-ocean-800/60 p-1">
          {PLATFORMS.map((p) => (
            <button key={p.code} onClick={() => setPlatform(p.code)}
                    className={`rounded-md px-4 py-1.5 text-sm transition ${
                      platform === p.code
                        ? 'bg-jade-500/15 text-jade-400'
                        : 'text-gray-400 hover:text-white'}`}>
              {p.label}
            </button>
          ))}
        </div>
      </div>

      {/* 授权流程：Shopee/Lazada 走授权链接；Shopify 走 token 直连 */}
      {current.mode === 'token' ? (
        <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }}
                    className={cardCls}>
          <div className="flex items-center gap-2 text-white">
            <KeyRound className="h-4 w-4 text-jade-400" />
            <h2 className="font-medium">连接店铺（自定义 App token 直连）</h2>
          </div>
          <p className="mt-2 text-xs leading-relaxed text-gray-400">
            在 Shopify 店铺后台 Settings → Apps and sales channels → Develop apps
            创建自定义 App，勾选 read_products / read_orders 等 scope，Install 后
            复制 Admin API access token（shpat_ 开头）粘贴到这里。测试可用免费的
            Partner development store——后端会真实调用 /shop.json 验证，
            验证通过才落归属。
          </p>
          <form className="mt-3 space-y-3" onSubmit={(e) => { e.preventDefault(); void doConnect(); }}>
            <input className={inputCls} value={shShop} autoComplete="off"
                   onChange={(e) => setShShop(e.target.value)}
                   placeholder="店铺域名（my-store.myshopify.com 或裸名 my-store）" />
            <input className={inputCls} type="password" value={shToken} autoComplete="off"
                   onChange={(e) => setShToken(e.target.value)}
                   placeholder="Admin API access token（shpat_…）" />
            <button type="submit" disabled={!shShop.trim() || !shToken.trim() || connecting}
                    className="rounded-lg bg-jade-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-jade-500 disabled:opacity-40">
              {connecting ? '验证并连接中…' : '连接店铺'}
            </button>
          </form>
          {shMsg && <div className="mt-3 text-xs text-jade-400">{shMsg}</div>}
          {shError && <div className="mt-3 text-xs text-red-400">{shError}</div>}
        </motion.div>
      ) : (
        <>
          <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }}
                      className={cardCls}>
            <div className="flex items-center gap-2 text-white">
              <ExternalLink className="h-4 w-4 text-jade-400" />
              <h2 className="font-medium">第一步 · 获取授权链接</h2>
            </div>
            <p className="mt-2 text-xs leading-relaxed text-gray-400">
              点击下方按钮生成 {current.label} 授权页链接，在新窗口打开并登录卖家账号确认授权。
              授权完成后回调地址会带一次性 code（约 5 分钟有效）——把它粘贴到第二步。
              若平台后台把回调地址配置为本应用的「授权回调页」（建议值见
              docs/ops-readiness.md），确认授权后会自动接住 code，无需手动复制。
            </p>
            <div className="mt-3 flex gap-2">
              <button onClick={getAuthUrl}
                      className="rounded-lg bg-jade-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-jade-500">
                生成授权链接
              </button>
              {authUrl && (
                <a href={authUrl} target="_blank" rel="noreferrer"
                   className="flex items-center gap-1.5 rounded-lg border border-jade-500/40 px-4 py-2 text-sm text-jade-400 transition hover:bg-jade-500/10">
                  打开授权页<ExternalLink size={14} />
                </a>
              )}
            </div>
            {authUrlError && (
              <div className="mt-3 flex items-start gap-1.5 rounded-lg border border-gold-500/30 bg-gold-500/5 px-3 py-2 text-xs text-gold-400">
                <AlertTriangle size={14} className="mt-0.5 shrink-0" />{authUrlError}
              </div>
            )}
            {authUrl && (
              <p className="mt-2 break-all text-xs text-gray-600">{authUrl}</p>
            )}
          </motion.div>

          <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }}
                      transition={{ delay: 0.05 }} className={cardCls}>
            <div className="flex items-center gap-2 text-white">
              <KeyRound className="h-4 w-4 text-jade-400" />
              <h2 className="font-medium">第二步 · 用 code 换店铺令牌</h2>
            </div>
            <form className="mt-3 space-y-3" onSubmit={(e) => { e.preventDefault(); void doExchange(); }}>
              <input className={inputCls} value={code} autoComplete="off"
                     onChange={(e) => setCode(e.target.value)}
                     placeholder={`${current.codeLabel}（从回调地址 code= 参数复制）`} />
              <input className={inputCls} value={extra} autoComplete="off"
                     onChange={(e) => setExtra(e.target.value)}
                     placeholder={current.extraLabel} />
              <button type="submit" disabled={!code.trim() || exchanging}
                      className="rounded-lg bg-jade-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-jade-500 disabled:opacity-40">
                {exchanging ? '换令牌中…' : '完成授权'}
              </button>
            </form>
            {exchangeMsg && <div className="mt-3 text-xs text-jade-400">{exchangeMsg}</div>}
            {exchangeError && <div className="mt-3 text-xs text-red-400">{exchangeError}</div>}
          </motion.div>
        </>
      )}

      {/* 店铺清单 */}
      <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }}
                  transition={{ delay: 0.1 }} className={cardCls}>
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2 text-white">
            <Store className="h-4 w-4 text-jade-400" />
            <h2 className="font-medium">已授权店铺（仅本租户可见）</h2>
          </div>
          <button onClick={() => void loadShops(platform)} disabled={loadingShops}
                  className="flex items-center gap-1.5 rounded-lg border border-white/10 px-3 py-1.5 text-xs text-gray-300 transition hover:bg-white/5">
            <RefreshCw className={`h-3.5 w-3.5 ${loadingShops ? 'animate-spin' : ''}`} />刷新
          </button>
        </div>
        {shopsError && <div className="mt-3 text-xs text-red-400">{shopsError}</div>}
        {shops && shops.length === 0 && (
          <p className="mt-3 text-xs text-gray-500">
            还没有授权过 {current.label} 店铺——按上面两步操作后这里会出现。
          </p>
        )}
        {shops && shops.length > 0 && (
          <table className="mt-3 w-full text-left text-xs">
            <thead className="text-gray-500">
              <tr className="border-b border-white/5">
                <th className="py-2 pr-4 font-normal">店铺 ID</th>
                <th className="py-2 pr-4 font-normal">授权时间 (UTC)</th>
                <th className="py-2 font-normal">令牌状态</th>
              </tr>
            </thead>
            <tbody className="text-gray-300">
              {shops.map((s) => (
                <tr key={s.shop_id || '_pending'} className="border-b border-white/5 last:border-0">
                  <td className="py-2 pr-4 font-mono">
                    {s.shop_id || <span className="text-gray-500">（待联调确认）</span>}
                  </td>
                  <td className="py-2 pr-4">{s.authorized_at}</td>
                  <td className="py-2">
                    {s.token_alive
                      ? <span className="text-jade-400">可用</span>
                      : <span className="text-gold-400">需重新授权（服务重启或已过期）</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </motion.div>

      {/* 店铺级调用 */}
      <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }}
                  transition={{ delay: 0.15 }} className={cardCls}>
        <div className="flex items-center gap-2 text-white">
          <Play className="h-4 w-4 text-jade-400" />
          <h2 className="font-medium">店铺级 API 调用</h2>
        </div>
        <p className="mt-2 text-xs leading-relaxed text-gray-400">
          经后端网关调用：只放行本租户已授权店铺，且仅限店铺级业务接口
          （商品 / 订单 / 物流 / 店铺信息），auth 类接口不允许。平台业务错误
          原样透传（502 前缀 [error_code]）。
        </p>
        <div className="mt-3 space-y-3">
          <div className="flex gap-2">
            <select value={callMethod} onChange={(e) => setCallMethod(e.target.value as 'GET' | 'POST')}
                    className="rounded-lg border border-white/10 bg-ocean-800 px-3 py-2.5 text-sm text-white outline-none focus:border-jade-500">
              <option value="GET">GET</option>
              <option value="POST">POST</option>
            </select>
            <input className={inputCls} value={callPath} autoComplete="off"
                   onChange={(e) => setCallPath(e.target.value)}
                   placeholder={platform === 'shopee'
                     ? '/api/v2/shop/get_shop_info'
                     : platform === 'shopify'
                       ? '/shop.json 或 /products.json（Admin API 路径，版本前缀由后端拼）'
                       : '/seller/get（具体接口以平台文档为准，联调确认前请勿依赖）'} />
          </div>
          <textarea className={`${inputCls} font-mono`} rows={3} value={callParams}
                    onChange={(e) => setCallParams(e.target.value)}
                    placeholder='{"item_id": "123"}（JSON 对象，可空）' />
          <button onClick={() => void doCall()} disabled={calling || !callPath.trim()}
                  className="rounded-lg bg-jade-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-jade-500 disabled:opacity-40">
            {calling ? '调用中…' : '发起调用'}
          </button>
        </div>
        {callError && <div className="mt-3 text-xs text-red-400">{callError}</div>}
        {callResult && (
          <pre className="mt-3 max-h-72 overflow-auto rounded-lg border border-white/10 bg-ocean-900 p-3 font-mono text-xs leading-relaxed text-gray-300">
            {callResult}
          </pre>
        )}
      </motion.div>

      {/* 边界说明（诚实性） */}
      <div className="rounded-xl border border-white/5 bg-ocean-800/30 p-4 text-xs leading-relaxed text-gray-500">
        <p>· 令牌缓存在服务进程内，服务重启后需重新授权/连接（授权关系本身持久保留）。</p>
        <p>· Shopify：自定义 App token 长期有效，缓存给 180 天保鲜窗口，到期重贴即可（幂等）；连接经过真实 API 验证。</p>
        <p>· Lazada 授权响应的 shop_id 形态待真实联调确认，当前可能显示空占位——不猜字段。</p>
        <p>· 平台接口的实际可用性以联调为准：签名/信封逻辑已有测试覆盖，但业务接口未经真实服务端验证（Shopify 连接验证除外）。</p>
      </div>
    </div>
  );
}
