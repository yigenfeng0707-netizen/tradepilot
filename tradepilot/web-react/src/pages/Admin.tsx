import { useEffect, useState } from 'react';
import { motion } from 'framer-motion';
import {
  Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';
import { KeyRound, RefreshCw, Users, Coins, Database, Activity } from 'lucide-react';
import {
  fetchAdminOverview, fetchAdminTrends,
  type AdminOverview, type AdminTrends,
} from '@/utils/api';

/**
 * 运营后台（M4）。
 *
 * 认证：X-Admin-Token（env TRADEPILOT_ADMIN_TOKEN），独立于租户 JWT——
 * 运营者是平台方，不是任何租户的成员。令牌只存 sessionStorage
 * （标签页关了就清），绝不进 localStorage。
 * fail closed：服务端没配令牌时这里只会看到"未启用"——如实转述，不粉饰。
 */

const TOKEN_KEY = 'tradepilot_admin_token';

const cardCls =
  'rounded-xl border border-white/10 bg-ocean-800/60 p-5';
const statCls = 'text-2xl font-semibold text-white';

function Stat({ icon, label, value, sub }: {
  icon: React.ReactNode; label: string; value: string | number; sub?: string;
}) {
  return (
    <div className={cardCls}>
      <div className="flex items-center gap-2 text-xs text-gray-400">
        {icon}{label}
      </div>
      <div className={`${statCls} mt-2`}>{value}</div>
      {sub && <div className="mt-1 text-xs text-gray-500">{sub}</div>}
    </div>
  );
}

export default function Admin() {
  const [token, setToken] = useState(() => sessionStorage.getItem(TOKEN_KEY) || '');
  const [data, setData] = useState<AdminOverview | null>(null);
  const [trends, setTrends] = useState<AdminTrends | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const load = async (t: string) => {
    setError('');
    setLoading(true);
    try {
      const d = await fetchAdminOverview(t);
      setData(d);
      sessionStorage.setItem(TOKEN_KEY, t);
      // 趋势与总览分开拉：图表失败不应清掉总览
      try {
        setTrends(await fetchAdminTrends(t));
      } catch {
        setTrends(null);    // 看板失败如实留空，不用旧数据硬撑
      }
    } catch (err) {
      setData(null);
      sessionStorage.removeItem(TOKEN_KEY);   // 失效令牌不留着反复撞
      setError(err instanceof Error ? err.message : '加载失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    // 挂载时若有已存令牌就自动加载一次；失效会在此处被清掉
    const saved = sessionStorage.getItem(TOKEN_KEY);
    if (saved) void load(saved);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (!data) {
    return (
      <div className="mx-auto max-w-md pt-20">
        <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }}
                    className={cardCls}>
          <div className="flex items-center gap-2 text-white">
            <KeyRound className="h-5 w-5 text-jade-400" />
            <h1 className="text-lg font-semibold">运营后台</h1>
          </div>
          <p className="mt-2 text-xs leading-relaxed text-gray-400">
            平台级聚合数据，独立于卖家账号——用服务端
            <code className="mx-1 rounded bg-white/10 px-1">TRADEPILOT_ADMIN_TOKEN</code>
            登入。令牌只存本标签页（sessionStorage）。
          </p>
          <form className="mt-4" onSubmit={(e) => { e.preventDefault(); load(token); }}>
            <input type="password" value={token} autoComplete="off"
                   onChange={(e) => setToken(e.target.value)}
                   placeholder="运营令牌"
                   className="w-full rounded-lg border border-white/10 bg-ocean-800 px-4 py-2.5 text-sm text-white placeholder-gray-500 outline-none transition focus:border-jade-500 focus:ring-1 focus:ring-jade-500/30" />
            {error && <div className="mt-2 text-xs text-red-400">{error}</div>}
            <button type="submit" disabled={!token || loading}
                    className="mt-3 w-full rounded-lg bg-jade-600 px-4 py-2.5 text-sm font-medium text-white transition hover:bg-jade-500 disabled:opacity-40">
              {loading ? '验证中…' : '进入'}
            </button>
          </form>
        </motion.div>
      </div>
    );
  }

  const sysAlarm = data.system.alarms.length > 0;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-white">运营总览</h1>
          <p className="text-xs text-gray-400">
            配额日 {data.quota.day} · 运行 {Math.round(data.system.uptime_s)}s
          </p>
        </div>
        <button onClick={() => load(token)} disabled={loading}
                className="flex items-center gap-1.5 rounded-lg border border-white/10 px-3 py-1.5 text-xs text-gray-300 transition hover:bg-white/5">
          <RefreshCw className={`h-3.5 w-3.5 ${loading ? 'animate-spin' : ''}`} />刷新
        </button>
      </div>

      {sysAlarm && (
        <div className="rounded-lg border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-300">
          告警：{data.system.alarms.join('、')}
        </div>
      )}

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Stat icon={<Users className="h-4 w-4" />} label="租户 / 用户"
              value={`${data.auth.tenants} / ${data.auth.users}`}
              sub={`活跃刷新令牌 ${data.auth.active_refresh_tokens}`} />
        <Stat icon={<Coins className="h-4 w-4" />} label="今日配额消耗"
              value={data.quota.total_weight}
              sub={`${data.quota.active_tenants} 个租户活跃 · 每租户上限 ${data.quota.limit_per_tenant}`} />
        <Stat icon={<Database className="h-4 w-4" />} label="存证 / 任务"
              value={`${data.provenance.total} / ${data.missions_total}`}
              sub={`来源 ${data.provenance.distinct_sources} 个`} />
        <Stat icon={<Activity className="h-4 w-4" />} label="系统"
              value={`${data.system.requests_total} 请求`}
              sub={`5xx ${data.system.errors_5xx} · LLM ${data.llm_online ? `在线（${data.llm_model}）` : '离线'}`} />
      </div>

      <div className={cardCls}>
        <h2 className="text-sm font-medium text-white">租户列表</h2>
        <div className="mt-3 overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead className="text-gray-400">
              <tr>
                <th className="pb-2 pr-4 font-medium">公司</th>
                <th className="pb-2 pr-4 font-medium">内部名</th>
                <th className="pb-2 font-medium">注册时间</th>
              </tr>
            </thead>
            <tbody className="text-gray-200">
              {data.tenants.tenants.map((t) => (
                <tr key={t.id} className="border-t border-white/5">
                  <td className="py-2 pr-4">{t.company || '—'}</td>
                  <td className="py-2 pr-4 text-gray-400">{t.name}</td>
                  <td className="py-2 text-gray-400">{t.created_at}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {trends && (
        <div className="grid gap-4 lg:grid-cols-2">
          <div className={cardCls}>
            <h2 className="text-sm font-medium text-white">任务量（近 {trends.days} 天）</h2>
            <p className="mt-1 text-xs text-gray-500">
              按任务开始时间聚合；平线 = 真的没有任务，不是缺数据。
            </p>
            <div className="mt-3 h-52">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={trends.missions_by_day}>
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" />
                  <XAxis dataKey="day" tick={{ fill: '#9ca3af', fontSize: 10 }}
                         tickFormatter={(d: string) => d.slice(5)} />
                  <YAxis allowDecimals={false} tick={{ fill: '#9ca3af', fontSize: 10 }} />
                  <Tooltip contentStyle={{ background: '#12212e', border: 'none' }} />
                  <Bar dataKey="missions" fill="#22c55e" radius={[3, 3, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          </div>

          <div className={cardCls}>
            <h2 className="text-sm font-medium text-white">配额消耗（近 {trends.days} 天）</h2>
            <p className="mt-1 text-xs text-gray-500">
              按请求权重累计；上线前的天数如实为 0，不回填不估算。
            </p>
            <div className="mt-3 h-52">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={trends.quota_by_day}>
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" />
                  <XAxis dataKey="day" tick={{ fill: '#9ca3af', fontSize: 10 }}
                         tickFormatter={(d: string) => d.slice(5)} />
                  <YAxis allowDecimals={false} tick={{ fill: '#9ca3af', fontSize: 10 }} />
                  <Tooltip contentStyle={{ background: '#12212e', border: 'none' }} />
                  <Bar dataKey="weight" fill="#eab308" radius={[3, 3, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          </div>

          <div className={`${cardCls} lg:col-span-2`}>
            <h2 className="text-sm font-medium text-white">存证类型分布</h2>
            <div className="mt-3 flex flex-wrap gap-2">
              {Object.entries(trends.provenance_by_kind).map(([kind, n]) => (
                <span key={kind}
                      className="rounded-full border border-white/10 bg-white/5 px-3 py-1 text-xs text-gray-200">
                  {kind} · {n}
                </span>
              ))}
              {Object.keys(trends.provenance_by_kind).length === 0 && (
                <span className="text-xs text-gray-500">（暂无存证）</span>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
