import { useMemo, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { motion } from 'framer-motion';
import { CheckCircle2, ExternalLink, KeyRound, XCircle } from 'lucide-react';
import { exchangeIntegrationCode } from '@/utils/api';
import { useStore } from '@/store/useStore';

/**
 * 授权回调承接页（/integrations/callback）。
 *
 * 平台授权完成后跳回 redirect 地址并带一次性 code（约 5 分钟有效）。
 * 若平台后台把回调地址配置为本页（建议值见 docs/ops-readiness.md），
 * 卖家点完"确认授权"就会回到这里，自动取出 code 完成换令牌——
 * 不用再手动从地址栏复制。
 *
 * 诚实边界：
 * - Shopee/Lazada 的回调 query 参数名是文档口径，实际字段以联调为准——
 *   缺 code 就如实说"回调没带 code"，不猜、不重试（code 一次性）；
 * - 回调参数里没有可靠的平台标识，平台由用户确认（默认 Shopee）；
 * - 未登录时 code 已可能失效（一次性），不假装能续用——请重新发起授权。
 */

const cardCls = 'rounded-xl border border-white/10 bg-ocean-800/60 p-5';
const inputCls =
  'w-full rounded-lg border border-white/10 bg-ocean-800 px-4 py-2.5 text-sm ' +
  'text-white placeholder-gray-500 outline-none transition focus:border-jade-500 ' +
  'focus:ring-1 focus:ring-jade-500/30';

const PLATFORMS = [
  { code: 'shopee', label: 'Shopee', extraLabel: 'main_merchant_id（可空）' },
  { code: 'lazada', label: 'Lazada', extraLabel: 'redirect / uuid（可空）' },
] as const;

export default function IntegrationCallback() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const { isAuthenticated } = useStore();

  // code 一次性：初始读一次就固定下来，避免用户改参数造成重放困惑
  const query = useMemo(() => ({
    code: params.get('code') || '',
    shopId: params.get('shop_id') || '',
    mainMerchantId: params.get('main_merchant_id') || '',
  }), [params]);

  const [platform, setPlatform] = useState<'shopee' | 'lazada'>('shopee');
  const [code, setCode] = useState(query.code);
  const [extra, setExtra] = useState(
    platform === 'shopee' ? query.mainMerchantId : '');
  const [result, setResult] = useState('');
  const [error, setError] = useState('');
  const [exchanging, setExchanging] = useState(false);

  const switchPlatform = (p: 'shopee' | 'lazada') => {
    setPlatform(p);
    setExtra(p === 'shopee' ? query.mainMerchantId : '');
  };

  const doExchange = async () => {
    setError(''); setResult('');
    setExchanging(true);
    try {
      const r = await exchangeIntegrationCode(platform, {
        code: code.trim(),
        main_merchant_id: platform === 'shopee' ? extra.trim() : undefined,
        redirect: platform === 'lazada' ? extra.trim() : undefined,
      });
      setResult(
        r.shop_id
          ? `授权成功，店铺 ${r.shop_id} 已归属当前租户`
          : '授权成功，已归属当前租户（shop_id 待联调确认）');
    } catch (err) {
      // 平台错误码原样透传；code 失效是最常见原因（一次性、约 5 分钟）
      setError(err instanceof Error ? err.message : '换令牌失败');
    } finally {
      setExchanging(false);
    }
  };

  return (
    <div className="mx-auto max-w-md pt-16">
      <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }}
                  className={cardCls}>
        <div className="flex items-center gap-2 text-white">
          <KeyRound className="h-5 w-5 text-jade-400" />
          <h1 className="text-lg font-semibold">完成店铺授权</h1>
        </div>

        {!query.code && (
          <div className="mt-4 flex items-start gap-2 rounded-lg border border-gold-500/30 bg-gold-500/5 px-3 py-2.5 text-xs text-gold-400">
            <XCircle size={14} className="mt-0.5 shrink-0" />
            <span>回调地址没带 code 参数——授权流程可能没有完成，或平台回调参数与预期不同。
              可回到<Link className="mx-0.5 underline" to="/integrations">店铺集成页</Link>重新发起。</span>
          </div>
        )}

        {query.code && !isAuthenticated && (
          <div className="mt-4 rounded-lg border border-gold-500/30 bg-gold-500/5 px-3 py-2.5 text-xs text-gold-400">
            检测到授权 code，但当前未登录。授权归属租户，请先登录——
            不过 code 是一次性的（约 5 分钟失效），登录后大概率需要
            <Link className="mx-0.5 underline" to="/integrations">重新发起授权</Link>。
          </div>
        )}

        {query.code && isAuthenticated && (
          <>
            <p className="mt-3 text-xs leading-relaxed text-gray-400">
              已从回调地址取出 code{query.shopId ? `（店铺 ${query.shopId}）` : ''}。
              确认平台后直接完成换令牌。
            </p>
            <div className="mt-3 flex gap-1 rounded-lg border border-white/10 bg-ocean-800/60 p-1">
              {PLATFORMS.map((p) => (
                <button key={p.code} onClick={() => switchPlatform(p.code)}
                        className={`flex-1 rounded-md px-3 py-1.5 text-sm transition ${
                          platform === p.code
                            ? 'bg-jade-500/15 text-jade-400'
                            : 'text-gray-400 hover:text-white'}`}>
                  {p.label}
                </button>
              ))}
            </div>
            <input className={`${inputCls} mt-3`} value={code}
                   onChange={(e) => setCode(e.target.value)}
                   placeholder="授权 code（一次性）" />
            <input className={`${inputCls} mt-2`} value={extra}
                   onChange={(e) => setExtra(e.target.value)}
                   placeholder={PLATFORMS.find((p) => p.code === platform)!.extraLabel} />
            <button onClick={() => void doExchange()} disabled={!code.trim() || exchanging}
                    className="mt-3 w-full rounded-lg bg-jade-600 px-4 py-2.5 text-sm font-medium text-white transition hover:bg-jade-500 disabled:opacity-40">
              {exchanging ? '换令牌中…' : '完成授权'}
            </button>
          </>
        )}

        {result && (
          <div className="mt-4 flex items-start gap-2 rounded-lg border border-jade-500/30 bg-jade-500/5 px-3 py-2.5 text-xs text-jade-400">
            <CheckCircle2 size={14} className="mt-0.5 shrink-0" />
            <span>{result}</span>
          </div>
        )}
        {error && (
          <div className="mt-3 rounded-lg border border-red-500/30 bg-red-500/5 px-3 py-2.5 text-xs text-red-400">
            {error}
          </div>
        )}

        <div className="mt-4 flex items-center justify-between border-t border-white/5 pt-3">
          <Link to="/integrations" className="text-xs text-gray-400 underline hover:text-white">
            返回店铺集成
          </Link>
          {result && (
            <button onClick={() => navigate('/integrations')}
                    className="flex items-center gap-1 text-xs text-jade-400 hover:text-jade-300">
              去查看店铺清单<ExternalLink size={12} />
            </button>
          )}
        </div>
      </motion.div>
    </div>
  );
}
