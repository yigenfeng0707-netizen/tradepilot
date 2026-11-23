import { useMemo, useRef, useState } from 'react';
import { motion } from 'framer-motion';
import {
  ChevronDown, Loader2, Play, CheckCircle2, Circle, BarChart3, Target,
  Sparkles, ShieldCheck, RefreshCw, AlertTriangle, SkipForward,
} from 'lucide-react';
import { categories } from '@/store/useStore';
import { streamPipeline, type PipelineEvent } from '@/utils/api';

const regions = ['欧美', '东南亚'];
const budgets = ['低', '中', '高'];
const countries = ['美国', '印尼', '泰国', '越南', '马来西亚', '菲律宾', '新加坡'];
const platforms = ['shopee', 'amazon', 'tiktok', 'lazada'];
const langs = ['en', 'th', 'vi', 'id', 'ms'];

interface StepInfo { key: string; label: string; icon: typeof BarChart3; hint: string }
const steps: StepInfo[] = [
  { key: 'market_insight', label: '市场洞察', icon: BarChart3, hint: '真实抓取竞品与价格带' },
  { key: 'smart_selection', label: '智能选品', icon: Target, hint: '机会分排序 + 定价沙盘' },
  { key: 'content_generation', label: '内容生成', icon: Sparkles, hint: '多语言上架文案（60–90 秒）' },
  { key: 'compliance', label: '合规查询', icon: ShieldCheck, hint: '美国现场核验官方条款' },
];

interface PipelineSummary {
  product?: string;
  steps_completed: number;
  total_steps: number;
  errors: number;
  duration_seconds: number;
  failed_steps?: string[];
}

type Status = 'pending' | 'running' | 'done' | 'failed' | 'skipped';

interface StepState {
  status: Status;
  duration?: number;
  quality?: string;
  message?: string;
  data?: unknown;
}

const emptyStates = (): Record<string, StepState> =>
  Object.fromEntries(steps.map((s) => [s.key, { status: 'pending' as Status }]));

export default function Pipeline() {
  const [category, setCategory] = useState(categories[0]);
  const [keyword, setKeyword] = useState('');
  const [region, setRegion] = useState(regions[0]);
  const [budget, setBudget] = useState('中');
  const [targetCountry, setTargetCountry] = useState(countries[0]);
  const [platform, setPlatform] = useState('shopee');
  const [targetLang, setTargetLang] = useState('en');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [stepStates, setStepStates] = useState<Record<string, StepState>>(emptyStates);
  const [summary, setSummary] = useState<PipelineSummary | null>(null);
  const [note, setNote] = useState('');
  // 归档回执：任务 id 供「任务可回放」叙事现场点开 /api/missions/{id}
  const [archive, setArchive] = useState<{ mission_id?: string; error?: string | null }>({});
  const abortRef = useRef<AbortController | null>(null);

  const completedCount = useMemo(
    () => steps.filter((s) => stepStates[s.key]?.status === 'done').length,
    [stepStates],
  );

  const handleRun = async () => {
    setLoading(true);
    setError('');
    setSummary(null);
    setNote('');
    setStepStates(emptyStates());
    const ac = new AbortController();
    abortRef.current = ac;

    try {
      await streamPipeline(
        {
          category, keyword: keyword.trim(), region, budget, target_country: targetCountry,
          platform, target_language: targetLang,
        },
        (ev) => {
          if (ev.event === 'done') {
            const s = (ev as unknown as { summary: PipelineSummary }).summary;
            setSummary(s);
            setNote(ev.note || '');
            setArchive({ mission_id: ev.mission_id, error: ev.archive_error });
            return;
          }
          if (ev.event === 'step' && ev.step) {
            setStepStates((prev) => ({
              ...prev,
              [ev.step as string]: {
                status: (ev.status || 'running') as Status,
                duration: ev.duration,
                quality: ev.quality,
                message: ev.message || ev.summary,
                data: ev.data,
              },
            }));
          }
        },
        ac.signal,
      );
    } catch (err) {
      if ((err as Error)?.name === 'AbortError') return;
      setError(err instanceof Error ? err.message : '全链路执行失败，请稍后重试');
    } finally {
      setLoading(false);
      abortRef.current = null;
    }
  };

  return (
    <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="space-y-6">
      {/* 表单 */}
      <div className="glass-light rounded-xl p-6 space-y-4">
        <h3 className="text-sm font-medium text-white flex items-center gap-2">
          <Play size={16} className="text-gold-400" /> 全链路工作流
        </h3>
        <div className="flex flex-wrap gap-4">
          {[
            { label: '品类', value: category, setter: setCategory, options: categories },
            { label: '区域', value: region, setter: setRegion, options: regions },
            { label: '预算', value: budget, setter: setBudget, options: budgets },
            { label: '目标国家', value: targetCountry, setter: setTargetCountry, options: countries },
            { label: '平台', value: platform, setter: setPlatform, options: platforms },
            { label: '语言', value: targetLang, setter: setTargetLang, options: langs },
          ].map((field) => (
            <div key={field.label}>
              <label className="text-xs text-gray-500 mb-1 block">{field.label}</label>
              <div className="relative">
                <select value={field.value} onChange={(e) => field.setter(e.target.value)}
                  className="appearance-none rounded-lg bg-ocean-800 px-3 py-2 pr-8 text-sm text-white border border-white/10 focus:border-jade-500 focus:outline-none">
                  {field.options.map((o) => <option key={o} value={o}>{o}</option>)}
                </select>
                <ChevronDown className="pointer-events-none absolute right-2 top-1/2 h-3 w-3 -translate-y-1/2 text-gray-400" />
              </div>
            </div>
          ))}
          {/* 检索词：后端只内置了 3 个品类的检索词，其余品类不传 keyword 时
              市场洞察/智能选品会诚实拒绝（不编造候选词）——留空即用内置词 */}
          <div>
            <label className="text-xs text-gray-500 mb-1 block">检索词（可选）</label>
            <input type="text" value={keyword}
              onChange={(e) => setKeyword(e.target.value)}
              placeholder="如 water bottle，非内置品类必填"
              className="rounded-lg bg-ocean-800 px-3 py-2 text-sm text-white border border-white/10 focus:border-jade-500 focus:outline-none w-52" />
          </div>
        </div>
        <div className="flex items-center gap-4">
          <button onClick={handleRun} disabled={loading}
            className="rounded-lg bg-jade-600 px-6 py-2.5 text-sm font-medium text-white hover:bg-jade-500 disabled:opacity-50 transition-colors flex items-center gap-2">
            {loading ? <Loader2 size={16} className="animate-spin" /> : <Play size={16} />}
            {loading ? '执行中...' : '运行全链路'}
          </button>
          <p className="text-xs text-gray-500">
            四个步骤串行执行，实测整链约 3–4 分钟（单次 LLM 文案就要 60–120 秒，
            美国合规要现场抓官方页面）。进度是真实的，不是定时器模拟的。
          </p>
        </div>
      </div>

      {error && (
        <div className="rounded-lg bg-red-500/10 border border-red-500/20 p-4 flex items-center justify-between">
          <span className="text-sm text-red-400">{error}</span>
          <button onClick={handleRun} disabled={loading}
            className="rounded-lg bg-red-500/20 px-3 py-1.5 text-xs text-red-400 hover:bg-red-500/30 transition-colors flex items-center gap-1">
            <RefreshCw size={12} /> 重试
          </button>
        </div>
      )}

      {/* 时间线 */}
      <div className="glass-light rounded-xl p-6">
        <div className="space-y-0">
          {steps.map((step, i) => {
            const st = stepStates[step.key] || { status: 'pending' as Status };
            const Icon = step.icon;
            const isLast = i === steps.length - 1;
            const tone =
              st.status === 'done' ? 'bg-jade-500/20 text-jade-400'
              : st.status === 'running' ? 'bg-gold-500/20 text-gold-400'
              : st.status === 'failed' ? 'bg-red-500/20 text-red-400'
              : st.status === 'skipped' ? 'bg-white/5 text-gray-500'
              : 'bg-ocean-800 text-gray-600';
            return (
              <div key={step.key}>
                <div className="flex items-start gap-4">
                  <div className="flex flex-col items-center">
                    <div className={`rounded-full p-2 transition-colors ${tone}`}>
                      {st.status === 'done' ? <CheckCircle2 size={20} />
                        : st.status === 'running' ? <Loader2 size={20} className="animate-spin" />
                        : st.status === 'failed' ? <AlertTriangle size={20} />
                        : st.status === 'skipped' ? <SkipForward size={20} />
                        : <Circle size={20} />}
                    </div>
                    {!isLast && (
                      <div className={`w-0.5 h-8 transition-colors ${
                        st.status === 'done' ? 'bg-jade-500/30' : 'bg-ocean-800'}`} />
                    )}
                  </div>
                  <div className="flex-1 pb-4">
                    <div className="flex flex-wrap items-center gap-2">
                      <Icon size={16} className={
                        st.status === 'done' ? 'text-jade-400'
                        : st.status === 'running' ? 'text-gold-400'
                        : st.status === 'failed' ? 'text-red-400' : 'text-gray-600'} />
                      <span className={`text-sm font-medium ${
                        st.status === 'done' ? 'text-white'
                        : st.status === 'running' ? 'text-gold-400'
                        : st.status === 'failed' ? 'text-red-400' : 'text-gray-500'}`}>
                        {step.label}
                      </span>
                      {st.status === 'running' && <span className="text-xs text-gold-400">执行中…</span>}
                      {st.duration != null && st.status !== 'running' && (
                        <span className="text-xs text-gray-600">{st.duration}s</span>
                      )}
                      {st.quality && (
                        <span className="rounded bg-white/5 px-1.5 py-0.5 text-[10px] text-gray-400">
                          口径 {st.quality}
                        </span>
                      )}
                    </div>
                    {st.status === 'pending' && (
                      <p className="mt-1 text-xs text-gray-600">{step.hint}</p>
                    )}
                    {st.status === 'skipped' && (
                      <p className="mt-1 text-xs text-gray-500">已跳过：{st.message}</p>
                    )}
                    {st.status === 'failed' && (
                      <p className="mt-1 text-xs text-red-400">{st.message}</p>
                    )}
                    {st.status === 'done' && st.data != null && (
                      <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }}
                        className="mt-2 rounded-lg bg-ocean-800/50 p-3">
                        <StepSummary stepKey={step.key} data={st.data} />
                      </motion.div>
                    )}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* 总览 */}
      {summary && (
        <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }}
          className="glass-light rounded-xl p-6">
          <h3 className="text-sm font-medium text-white mb-4">执行总览</h3>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <SummaryCard label="完成步骤" value={`${summary.steps_completed}/${summary.total_steps}`} />
            <SummaryCard label="耗时" value={`${summary.duration_seconds.toFixed(1)} 秒`} />
            <SummaryCard label="失败步骤" value={summary.errors ? String(summary.errors) : '0'}
              highlight={summary.errors > 0} />
            <SummaryCard label="推荐产品" value={summary.product || '-'} highlight />
          </div>
          {note && <p className="mt-3 text-xs text-gray-500">{note}</p>}
          {archive.mission_id && !archive.error && (
            <p className="mt-2 text-xs text-jade-400">
              任务已归档：<code>{archive.mission_id}</code>（在
              <code> /api/missions/{archive.mission_id} </code>可回放本次结果）
            </p>
          )}
          {archive.error && (
            <p className="mt-2 text-xs text-gold-400">
              本次任务未能归档：{archive.error}（结果仍完整展示，仅缺回放记录）
            </p>
          )}
          {completedCount < steps.length && (
            <p className="mt-1 text-xs text-gold-400">
              未全部完成：失败与跳过的步骤都在上面逐条写明了原因，没有任何一步用样本数据充数。
            </p>
          )}
        </motion.div>
      )}
    </motion.div>
  );
}

function SummaryCard({ label, value, highlight }: { label: string; value: string; highlight?: boolean }) {
  return (
    <div className={`rounded-lg p-3 ${highlight ? 'bg-jade-500/10 border border-jade-500/20' : 'bg-ocean-800/50'}`}>
      <p className="text-xs text-gray-500">{label}</p>
      <p className={`text-sm font-medium ${highlight ? 'text-jade-400' : 'text-white'}`}>{value}</p>
    </div>
  );
}

/** 每一步只展示该步骤**真实返回**的字段，不做推算、不补默认值。 */
function StepSummary({ stepKey, data }: { stepKey: string; data: unknown }) {
  const d = data as Record<string, any>;
  switch (stepKey) {
    case 'market_insight': {
      const band = d.price_band as { min: number; max: number; median: number } | undefined;
      const comps = (d.competitors as unknown[] | undefined) || [];
      // summary 在真实路径下是 {summary, trend_analysis, recommendation} 对象，
      // 样本路径下才是字符串——直接 String() 会渲染出 "[object Object]"。
      const sumText = typeof d.summary === 'string'
        ? d.summary
        : (d.summary?.summary as string | undefined) || '';
      return (
        <div className="text-xs text-gray-400 space-y-1">
          <p>检索词: <span className="text-white">{d.keyword_used || '-'}</span></p>
          {band ? (
            <p>价格带: <span className="text-white">
              {String(d.competitors?.[0]?.currency || '')} {band.min?.toFixed(2)}–{band.max?.toFixed(2)}
              （中位 {band.median?.toFixed(2)}）
            </span></p>
          ) : (
            <p>市场规模: <span className="text-gray-500">未接入数据源</span></p>
          )}
          <p>竞品条数: <span className="text-white">{comps.length}</span></p>
          {sumText && <p className="mt-1 italic text-jade-300">{sumText.slice(0, 90)}…</p>}
        </div>
      );
    }
    case 'smart_selection': {
      const score = d.overall_score as { value?: number; basis?: string } | undefined;
      const recs = (d.product_recommendations as Record<string, unknown>[] | undefined) || [];
      const profit = d.profit_analysis as { available?: boolean; reason?: string } | undefined;
      return (
        <div className="text-xs text-gray-400 space-y-1">
          <p>机会分: <span className="text-jade-400">{score?.value ?? '-'}</span>
            {score?.basis && <span className="ml-1 text-gray-600">（{score.basis}）</span>}</p>
          <p>Top1: <span className="text-white">{String(recs[0]?.product ?? '-')}</span></p>
          <p>利润分析: <span className={profit?.available ? 'text-jade-400' : 'text-gray-500'}>
            {profit?.available ? '已计算（定价沙盘）' : (profit?.reason || '未运行')}
          </span></p>
          <p>供应链: <span className="text-gray-500">未接入（1688/国际站需企业账号）</span></p>
        </div>
      );
    }
    case 'content_generation': {
      const content = d.content as { title?: string } | undefined;
      const warns = (d.platform_compliance?.warnings as string[] | undefined) || [];
      return (
        <div className="text-xs text-gray-400 space-y-1">
          <p>标题: <span className="text-white line-clamp-1">{content?.title || '-'}</span></p>
          <p>平台/语言: <span className="text-gold-400">{d.platform_label || d.platform}</span>
            <span className="text-gray-600"> / </span>
            <span className="text-white">{d.language_label || d.target_language}</span></p>
          <p>生成方式: <span className={String(d.generated_by).startsWith('llm') ? 'text-jade-400' : 'text-gray-400'}>
            {d.generated_by}</span></p>
          {warns.length > 0 && (
            <p className="text-gold-400">提醒: {warns[0]}</p>
          )}
        </div>
      );
    }
    case 'compliance': {
      return (
        <div className="text-xs text-gray-400 space-y-1">
          <p>目标国家: <span className="text-white">{d.target_country}</span></p>
          <p>已定位原文: <span className="text-jade-400">{d.verified_count}/{d.total}</span></p>
          <p>风险等级: <span className={
            d.risk_level === '高' ? 'text-red-400'
            : d.risk_level === '中' ? 'text-gold-400' : 'text-jade-400'}>{d.risk_level}</span></p>
          <p className="text-gray-600 line-clamp-2">{d.risk_basis}</p>
        </div>
      );
    }
    default:
      return null;
  }
}
