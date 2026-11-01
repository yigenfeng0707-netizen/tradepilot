import { useEffect, useState } from 'react';
import { motion } from 'framer-motion';
import {
  ChevronDown, Loader2, ShieldCheck, AlertTriangle, CheckCircle2,
  RefreshCw, ExternalLink, Link2, Info,
} from 'lucide-react';
import {
  fetchComplianceCheck, fetchComplianceOptions,
  type ComplianceResult, type ComplianceOption, type VerificationKind,
} from '@/utils/api';

/** 逐条验证状态的口径。颜色不是装饰：绿=已在官方页面定位到原文，
 *  灰=只是拿到了页面，红=连页面都没拿到，蓝=静态参考（没做过验证）。 */
const VERIFY_META: Record<VerificationKind, { label: string; cls: string; hint: string }> = {
  clause_matched: {
    label: '已定位原文', cls: 'text-jade-400 bg-jade-500/10 border-jade-500/20',
    hint: '已在官方页面正文中定位到该条款对应的原文片段',
  },
  page_only: {
    label: '仅页面可达', cls: 'text-gray-400 bg-white/5 border-white/10',
    hint: '页面能打开，但没定位到条款原文——不等于条款已验证',
  },
  blocked: {
    label: '被拦截', cls: 'text-red-400 bg-red-500/10 border-red-500/20',
    hint: '站点 WAF 拦截或返回空壳，未取得可核验正文',
  },
  offline: {
    label: '不可达', cls: 'text-red-400 bg-red-500/10 border-red-500/20',
    hint: '当前网络不可达或 robots 禁止抓取',
  },
  static_reference: {
    label: '静态参考', cls: 'text-gold-400 bg-gold-500/10 border-gold-500/20',
    hint: '来自静态条款库，未经官方页面验证',
  },
};

const QUALITY_LABEL: Record<string, string> = {
  real: '真实抓取（官方页面）',
  sample: '静态/样本参考（非实时验证）',
  generated: '生成内容',
};

export default function Compliance() {
  const [options, setOptions] = useState<ComplianceOption[]>([]);
  const [category, setCategory] = useState('宠物用品');
  const [country, setCountry] = useState('美国');
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<ComplianceResult | null>(null);
  const [error, setError] = useState('');
  const [tariffNote, setTariffNote] = useState('');

  useEffect(() => {
    fetchComplianceOptions()
      .then((o) => {
        setOptions(o.countries);
        setTariffNote(o.tariff_note);
      })
      .catch(() => setOptions([]));
  }, []);

  const handleCheck = async () => {
    setLoading(true);
    setError('');
    setResult(null);
    try {
      setResult(await fetchComplianceCheck(category, country));
    } catch (err) {
      setError(err instanceof Error ? err.message : '查询失败，请稍后重试');
    } finally {
      setLoading(false);
    }
  };

  const current = options.find((o) => o.name === country);
  const quality = result?.data_quality ?? '';

  return (
    <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="space-y-6">
      {/* 选择器 */}
      <div className="glass-light rounded-xl p-6 space-y-4">
        <h3 className="text-sm font-medium text-white flex items-center gap-2">
          <ShieldCheck size={16} className="text-jade-400" /> 合规查询
        </h3>
        <div className="flex flex-wrap gap-4 items-end">
          <div>
            <label className="text-xs text-gray-500 mb-1 block">品类</label>
            <div className="relative">
              <select value={category} onChange={(e) => setCategory(e.target.value)}
                className="appearance-none rounded-lg bg-ocean-800 px-4 py-2 pr-10 text-sm text-white border border-white/10 focus:border-jade-500 focus:outline-none">
                {['宠物用品', '家居用品', '3C电子'].map((c) => <option key={c} value={c}>{c}</option>)}
              </select>
              <ChevronDown className="pointer-events-none absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" />
            </div>
          </div>
          <div>
            <label className="text-xs text-gray-500 mb-1 block">目标国家</label>
            <div className="relative">
              <select value={country} onChange={(e) => setCountry(e.target.value)}
                className="appearance-none rounded-lg bg-ocean-800 px-4 py-2 pr-10 text-sm text-white border border-white/10 focus:border-jade-500 focus:outline-none">
                {(options.length ? options.map((o) => o.name) : ['美国']).map((c) => (
                  <option key={c} value={c}>{c}</option>
                ))}
              </select>
              <ChevronDown className="pointer-events-none absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" />
            </div>
          </div>
          <button onClick={handleCheck} disabled={loading}
            className="rounded-lg bg-jade-600 px-6 py-2 text-sm font-medium text-white hover:bg-jade-500 disabled:opacity-50 transition-colors flex items-center gap-2">
            {loading ? <Loader2 size={16} className="animate-spin" /> : <ShieldCheck size={16} />}
            检查合规
          </button>
        </div>
        {current && (
          <p className={`text-xs ${current.official_source ? 'text-jade-400' : 'text-gold-400'}`}>
            {current.official_source
              ? `${current.name}：官方源实时验证（现场抓官方页面并定位条款原文，通常 30–60 秒）`
              : `${current.name}：${current.note}`}
          </p>
        )}
      </div>

      {error && (
        <div className="rounded-lg bg-red-500/10 border border-red-500/20 p-4 flex items-center justify-between">
          <span className="text-sm text-red-400">{error}</span>
          <button onClick={handleCheck} disabled={loading}
            className="rounded-lg bg-red-500/20 px-3 py-1.5 text-xs text-red-400 hover:bg-red-500/30 transition-colors flex items-center gap-1">
            <RefreshCw size={12} /> 重试
          </button>
        </div>
      )}

      {loading && (
        <div className="flex h-32 items-center justify-center">
          <Loader2 className="h-8 w-8 animate-spin text-jade-500" />
          <span className="ml-3 text-gray-400">正在核验官方条款…</span>
        </div>
      )}

      {result && !loading && (
        <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} className="space-y-6">
          {/* 口径条 */}
          <div className={`rounded-xl border p-4 ${
            quality === 'real' ? 'border-jade-500/20 bg-jade-500/5' : 'border-gold-500/20 bg-gold-500/5'}`}>
            <p className={`text-xs ${quality === 'real' ? 'text-jade-400' : 'text-gold-400'}`}>
              数据口径：{QUALITY_LABEL[quality] || quality} · {result.note}
            </p>
            <p className="mt-1 text-xs text-gray-500">
              风险等级「{result.risk_level}」——{result.risk_basis}
            </p>
          </div>

          {/* 概览 */}
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {[
              { label: '条款总数', value: String(result.total) },
              { label: '已定位原文', value: `${result.verified_count}/${result.total}`, highlight: result.verified_count > 0 },
              { label: '强制要求', value: String(result.mandatory_count) },
              { label: '风险等级', value: result.risk_level, highlight: result.risk_level === '高' },
            ].map((c) => (
              <div key={c.label} className={`rounded-lg p-3 ${c.highlight ? 'bg-jade-500/10 border border-jade-500/20' : 'bg-ocean-800/50'}`}>
                <p className="text-xs text-gray-500">{c.label}</p>
                <p className={`text-sm font-medium ${c.highlight ? 'text-jade-400' : 'text-white'}`}>{c.value}</p>
              </div>
            ))}
          </div>

          {/* 条款逐条 */}
          <div className="glass-light rounded-xl p-6">
            <h4 className="text-sm font-medium text-white mb-3 flex items-center gap-2">
              <ShieldCheck size={14} className="text-jade-400" /> 条款与核验状态
            </h4>
            <div className="space-y-2">
              {result.checks.map((c, i) => {
                const meta = VERIFY_META[c.verification] || VERIFY_META.static_reference;
                const url = c.page_url || c.source_url || '';
                return (
                  <div key={i} className="rounded-lg bg-ocean-800/50 p-3">
                    <div className="flex items-start justify-between gap-3">
                      <div className="flex-1">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="text-sm text-gray-200">{c.item}</span>
                          <span className="rounded bg-white/5 px-1.5 py-0.5 text-[10px] text-gray-400">{c.level}</span>
                        </div>
                        <p className="mt-1 text-xs text-gray-500">{c.authority}</p>
                      </div>
                      <span
                        title={meta.hint}
                        className={`shrink-0 rounded border px-2 py-0.5 text-[10px] ${meta.cls}`}>
                        {meta.label}
                      </span>
                    </div>
                    {c.excerpt && (
                      <p className="mt-2 border-l-2 border-jade-500/30 pl-2 text-xs italic text-gray-500">
                        “{c.excerpt}”
                      </p>
                    )}
                    {url && (
                      <a href={url} target="_blank" rel="noreferrer"
                        className="mt-2 inline-flex items-center gap-1 text-[11px] text-jade-500 hover:text-jade-400">
                        <Link2 size={10} /> {url.replace(/^https?:\/\//, '').slice(0, 60)}
                        <ExternalLink size={10} />
                      </a>
                    )}
                    {c.note && <p className="mt-1 text-[11px] text-gold-500/80">{c.note}</p>}
                    {c.provenance_id != null && (
                      <p className="mt-1 text-[11px] text-gray-600">存证 #{c.provenance_id}</p>
                    )}
                  </div>
                );
              })}
            </div>
          </div>

          {/* 建议 */}
          {result.recommendations.length > 0 && (
            <div className="glass-light rounded-xl p-6">
              <h4 className="text-sm font-medium text-white mb-3 flex items-center gap-2">
                <CheckCircle2 size={14} className="text-jade-400" /> 下一步动作
              </h4>
              <div className="space-y-1">
                {result.recommendations.map((r, i) => (
                  <p key={i} className="text-xs text-gray-400">• {r}</p>
                ))}
              </div>
            </div>
          )}

          {/* 数据缺口 */}
          <div className="rounded-lg border border-gold-500/20 bg-gold-500/5 p-4">
            <p className="text-xs text-gold-400 mb-2 flex items-center gap-1">
              <AlertTriangle size={12} /> 本报告没有覆盖什么
            </p>
            {result.gaps.map((g, i) => (
              <p key={i} className="text-xs text-gray-400">• {g}</p>
            ))}
          </div>
        </motion.div>
      )}

      {/* 关税：没有真实数据源，不提供计算器 */}
      <div className="glass-light rounded-xl p-6">
        <h3 className="text-sm font-medium text-white flex items-center gap-2 mb-2">
          <Info size={16} className="text-gold-400" /> 关税计算
        </h3>
        <p className="text-xs text-gray-500">
          {tariffNote || 'HTS 与东盟各国税则无公开免费 API，不输出具体税率'}
          ——具体税率须由报关行按实际 HTS/HS 编码核定。这里不给估算值：
          一个凭感觉填的 12% 会让卖家把报价算错。
        </p>
      </div>
    </motion.div>
  );
}
