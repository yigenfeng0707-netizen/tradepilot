import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { motion } from 'framer-motion';
import {
  Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';
import { Gauge, LogIn } from 'lucide-react';
import { fetchMeUsage, type MeUsage } from '@/utils/api';
import { useStore } from '@/store/useStore';

/**
 * 我的用量（M4 计费闭环的用户侧）。
 *
 * 诚实口径：
 * - 配额关闭（limit=0）就写"未启用配额"，不把"没配"说成"没超"；
 * - token 账目只来自后端记账（llm.chat 深处真实读取的 usage 字段），
 *   缺 usage 字段的调用如实记 calls=1 + 0 token——不补不估；
 * - 近 7 天柱状图按天补零：平线 = 真的没用，不是缺数据。
 */

const cardCls = 'rounded-xl border border-white/10 bg-ocean-800/60 p-5';

const num = (n: number | undefined | null) =>
  (n ?? 0).toLocaleString('zh-CN');

export default function Account() {
  const { isAuthenticated } = useStore();
  const navigate = useNavigate();
  const [data, setData] = useState<MeUsage | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!isAuthenticated) return;
    setLoading(true);
    setError('');
    fetchMeUsage()
      .then(setData)
      .catch((err) => {
        setData(null);          // 失败如实留空，不拿旧账硬撑
        setError(err instanceof Error ? err.message : '加载失败');
      })
      .finally(() => setLoading(false));
  }, [isAuthenticated]);

  if (!isAuthenticated) {
    return (
      <div className="mx-auto max-w-md pt-20">
        <div className={cardCls}>
          <div className="flex items-center gap-2 text-white">
            <Gauge className="h-5 w-5 text-jade-400" />
            <h1 className="text-lg font-semibold">我的用量</h1>
          </div>
          <p className="mt-3 text-sm leading-relaxed text-gray-400">
            配额与 token 账目按租户归属——匿名请求没有账可查。
          </p>
          <button
            onClick={() => navigate('/login')}
            className="mt-4 flex w-full items-center justify-center gap-1.5 rounded-lg bg-jade-600 px-4 py-2.5 text-sm font-medium text-white transition hover:bg-jade-500"
          >
            <LogIn size={16} />先登录
          </button>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="mx-auto max-w-md pt-20 text-center text-sm text-red-400">
        {error}
      </div>
    );
  }

  if (!data) {
    return (
      <div className="mx-auto max-w-md pt-20 text-center text-sm text-gray-500">
        {loading ? '读取中…' : ''}
      </div>
    );
  }

  const { quota, llm_usage } = data;
  const today = llm_usage.today;
  const totalTokens = today.prompt_tokens + today.completion_tokens;

  // 后端 by_day 只有有账目的天——展示层补零，平线才表示"真的没用"
  const dayMap = new Map(llm_usage.by_day.map((d) => [d.day, d]));
  const chartData: { day: string; calls: number; tokens: number }[] = [];
  {
    const now = new Date();
    for (let i = 6; i >= 0; i--) {
      const d = new Date(now);
      d.setUTCDate(d.getUTCDate() - i);
      const key = d.toISOString().slice(0, 10);
      const row = dayMap.get(key);
      chartData.push({
        day: key.slice(5),
        calls: row?.calls ?? 0,
        tokens: (row?.prompt_tokens ?? 0) + (row?.completion_tokens ?? 0),
      });
    }
  }

  return (
    <div className="mx-auto max-w-4xl space-y-6">
      <div>
        <h1 className="text-xl font-semibold text-white">我的用量</h1>
        <p className="text-xs text-gray-400">租户 {data.tenant_id.slice(0, 8)}… · 记账时区 UTC</p>
      </div>

      {/* 配额 + 今日 token */}
      <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }}
                  className="grid gap-4 sm:grid-cols-3">
        <div className={cardCls}>
          <div className="flex items-center gap-2 text-xs text-gray-400">
            <Gauge className="h-3.5 w-3.5" />今日配额
          </div>
          {quota.limited ? (
            <>
              <div className="mt-2 text-2xl font-semibold text-white">
                {num(quota.used)}<span className="text-sm text-gray-500"> / {num(quota.limit)}</span>
              </div>
              <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-white/10">
                <div className="h-full rounded-full bg-jade-500 transition-all"
                     style={{ width: `${Math.min(100, ((quota.used ?? 0) / (quota.limit || 1)) * 100)}%` }} />
              </div>
              <div className="mt-1.5 text-xs text-gray-500">剩余 {num(quota.remaining)}</div>
            </>
          ) : (
            <div className="mt-2 text-sm text-gray-400">
              未启用配额（不限量）
              {quota.note && <div className="mt-1 text-xs text-gray-500">{quota.note}</div>}
            </div>
          )}
        </div>
        <div className={cardCls}>
          <div className="text-xs text-gray-400">今日 LLM 调用</div>
          <div className="mt-2 text-2xl font-semibold text-white">{num(today.calls)}</div>
          <div className="mt-1.5 text-xs text-gray-500">次数按记账口径，含缺 usage 字段的调用</div>
        </div>
        <div className={cardCls}>
          <div className="text-xs text-gray-400">今日 token</div>
          <div className="mt-2 text-2xl font-semibold text-white">{num(totalTokens)}</div>
          <div className="mt-1.5 text-xs text-gray-500">
            输入 {num(today.prompt_tokens)} / 输出 {num(today.completion_tokens)}
          </div>
        </div>
      </motion.div>

      {/* 近 7 天趋势 */}
      <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }}
                  transition={{ delay: 0.05 }} className={cardCls}>
        <h2 className="text-sm font-medium text-white">近 7 天（UTC，按天补零）</h2>
        <div className="mt-4 h-56">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={chartData}>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" />
              <XAxis dataKey="day" tick={{ fill: '#9ca3af', fontSize: 11 }} stroke="rgba(255,255,255,0.1)" />
              <YAxis tick={{ fill: '#9ca3af', fontSize: 11 }} stroke="rgba(255,255,255,0.1)" allowDecimals={false} />
              <Tooltip contentStyle={{ background: '#0f2733', border: '1px solid rgba(255,255,255,0.1)',
                                       borderRadius: 8, fontSize: 12 }}
                       labelStyle={{ color: '#e5e7eb' }} />
              <Bar dataKey="tokens" name="token 数" fill="#22c55e" radius={[3, 3, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
        {chartData.every((d) => d.tokens === 0) && (
          <p className="mt-1 text-xs text-gray-500">
            近 7 天没有 LLM 消耗——平线是真实记录，不是数据缺失。
          </p>
        )}
      </motion.div>

      {/* 口径说明 */}
      <div className="rounded-xl border border-white/5 bg-ocean-800/30 p-4 text-xs leading-relaxed text-gray-500">
        <p>· 配额按请求权重扣减（整链路 4 / 文案 2 / 其余 1），token 是成本口径——两套数字各管各的，不互相换算。</p>
        <p>· 账目自用量功能上线起才有，之前的天如实为 0，不回填不估算。</p>
        <p>· LLM 网关无 Key 时走规则模式，不产生 token，也不计费。</p>
      </div>
    </div>
  );
}
