import { useState, useEffect } from 'react';
import { motion } from 'framer-motion';
import { ChevronDown, Loader2, CheckCircle2, Circle, Truck, AlertTriangle, Info, Search } from 'lucide-react';
import { useStore, categories } from '@/store/useStore';
import { fetchSmartSelection, type SmartSelectionData } from '@/utils/api';
import ScoreCircle from '@/components/ScoreCircle';

const budgets = ['低', '中', '高'];
// 排序原则：有真实数据源的市场放前面。当前只有 US 可抓，SEA 待官方 API。
const markets = ['欧美', '东南亚', '中东', '日韩', '拉美', '非洲'];

// 只有这三类在工具层有内置候选词；其余品类必须自己给检索词。
const CATEGORIES_WITH_BUILTIN = ['宠物用品', '家居用品', '3C电子', '3C数码'];

export default function SmartSelection() {
  const { selectedCategory, setSelectedCategory, budget, setBudget, targetMarket, setTargetMarket } = useStore();
  const [data, setData] = useState<SmartSelectionData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // 关键词单独提交：每敲一个字就重新扫描会打爆目标站（每词一次真实抓取）。
  const [keywordInput, setKeywordInput] = useState('');
  const [keyword, setKeyword] = useState('');

  useEffect(() => {
    setLoading(true);
    setError(null);
    fetchSmartSelection(selectedCategory, budget, targetMarket, keyword)
      .then(setData)
      .catch((e: unknown) => {
        // 后端失败信封里的 message 是可直接展示的原因（如"该市场没有可抓取源"），
        // 必须原样呈现，不能吞成"暂无数据"——用户会以为是自己没选对。
        setData(null);
        setError(e instanceof Error ? e.message : String(e));
      })
      .finally(() => setLoading(false));
  }, [selectedCategory, budget, targetMarket, keyword]);

  const needsKeyword = !CATEGORIES_WITH_BUILTIN.includes(selectedCategory);

  // 选择器与检索词框**始终渲染**（加载中、出错时也在）：出错时若把表单一起替换掉，
  // 用户看到"该品类没有内置候选词"却无处输入，错误页就成了死路。
  const header = (
    <>
      <div className="flex flex-wrap gap-4">
        <div className="relative">
          <select value={selectedCategory} onChange={(e) => setSelectedCategory(e.target.value)}
            className="appearance-none rounded-lg bg-ocean-800 px-4 py-2 pr-10 text-sm text-white border border-white/10 focus:border-jade-500 focus:outline-none">
            {categories.map((c) => (<option key={c} value={c}>{c}</option>))}
          </select>
          <ChevronDown className="pointer-events-none absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" />
        </div>
        <div className="relative">
          <select value={budget} onChange={(e) => setBudget(e.target.value)}
            className="appearance-none rounded-lg bg-ocean-800 px-4 py-2 pr-10 text-sm text-white border border-white/10 focus:border-jade-500 focus:outline-none">
            {budgets.map((b) => (<option key={b} value={b}>预算: {b}</option>))}
          </select>
          <ChevronDown className="pointer-events-none absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" />
        </div>
        <div className="relative">
          <select value={targetMarket} onChange={(e) => setTargetMarket(e.target.value)}
            className="appearance-none rounded-lg bg-ocean-800 px-4 py-2 pr-10 text-sm text-white border border-white/10 focus:border-jade-500 focus:outline-none">
            {markets.map((m) => (<option key={m} value={m}>{m}</option>))}
          </select>
          <ChevronDown className="pointer-events-none absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" />
        </div>
      </div>

      {/* 检索词：内置假设词只覆盖三类，其余品类必须自己给词 */}
      <form
        onSubmit={(e) => { e.preventDefault(); setKeyword(keywordInput.trim()); }}
        className="flex flex-wrap items-center gap-2"
      >
        <div className="relative min-w-[260px] flex-1">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-500" />
          <input
            value={keywordInput}
            onChange={(e) => setKeywordInput(e.target.value)}
            placeholder={needsKeyword
              ? '该品类无内置候选词，请输入英文检索词（如 yoga mat）'
              : '可选：自定义检索词，留空则用内置假设词'}
            className="w-full rounded-lg border border-white/10 bg-ocean-800 py-2 pl-9 pr-4 text-sm text-white placeholder:text-gray-600 focus:border-jade-500 focus:outline-none"
          />
        </div>
        <button
          type="submit"
          className="rounded-lg border border-jade-500/30 bg-jade-500/15 px-4 py-2 text-sm text-jade-400 hover:bg-jade-500/25"
        >
          重新扫描
        </button>
        {needsKeyword && (
          <span className="text-xs text-gold-400">
            「{selectedCategory}」暂无内置候选词，不给检索词无法扫描
          </span>
        )}
      </form>
    </>
  );

  if (loading) {
    return (
      <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="space-y-6">
        {header}
        <div className="flex h-72 items-center justify-center">
          <Loader2 className="h-8 w-8 animate-spin text-jade-500" />
          <span className="ml-3 text-gray-400">正在真实扫描候选词（每词一次抓取，约 10-30 秒）...</span>
        </div>
      </motion.div>
    );
  }

  if (error) {
    return (
      <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="space-y-6">
        {header}
        <div className="glass-light rounded-xl p-6">
          <div className="flex items-start gap-3">
            <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-gold-400" />
            <div>
              <h3 className="text-sm font-medium text-white">无法给出选品评分</h3>
              <p className="mt-2 text-sm leading-relaxed text-gray-400">{error}</p>
              <p className="mt-3 text-xs text-gray-500">
                智能选品不使用样本数据兜底：用样本算出的排序会误导调研优先级，比不给结果更糟。
              </p>
            </div>
          </div>
        </div>
      </motion.div>
    );
  }

  if (!data) {
    return (
      <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="space-y-6">
        {header}
        <div className="flex h-72 items-center justify-center text-gray-500">暂无数据</div>
      </motion.div>
    );
  }

  const sortedProducts = [...data.product_recommendations].sort(
    (a, b) => (b.scores['综合评分'] || 0) - (a.scores['综合评分'] || 0)
  );

  const opportunityItems = [
    { label: '市场规模', value: data.market_opportunity.market_size },
    { label: '增长率', value: data.market_opportunity.growth_rate },
    { label: '竞争程度', value: data.market_opportunity.competition_level },
    { label: '进入难度', value: data.market_opportunity.entry_difficulty },
  ];

  const costItems = Object.entries(data.profit_analysis.cost_breakdown).map(([item, value]) => ({
    item, value,
  }));

  const revenueItems = Object.entries(data.profit_analysis.revenue).map(([item, value]) => ({
    item, value,
  }));

  const planPhases = Object.entries(data.action_plan).map(([key, phase]) => ({
    key, name: phase.name, tasks: phase.tasks,
  }));

  return (
    <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="space-y-6">
      {header}

      {/* 数据口径提示：评分是什么、不是什么，必须在结果最上方说清 */}
      <div className="flex items-start gap-2 rounded-lg border border-jade-500/20 bg-jade-500/5 p-3">
        <Info className="mt-0.5 h-4 w-4 shrink-0 text-jade-400" />
        <div className="text-xs leading-relaxed text-gray-400">
          <span className="text-jade-400">
            {data.data_quality === 'real' ? '真实抓取数据' : '样本数据'}
          </span>
          {' · '}
          评分衡量的是<span className="text-white">本批样本的分析价值与定位空间</span>，
          不是需求量、销量或搜索量（这三项均未接入数据源）。
          {data.overall_score.basis && <span className="block mt-1 text-gray-500">{data.overall_score.basis}</span>}
          {data.budget_note && <span className="block mt-1 text-gray-500">{data.budget_note}</span>}
        </div>
      </div>

      {/* Overall Score + Market Opportunity */}
      <div className="grid gap-6 lg:grid-cols-3">
        <div className="glass-light rounded-xl p-6 flex flex-col items-center justify-center">
          <p className="text-sm text-gray-400 mb-3">综合评分</p>
          <ScoreCircle score={data.overall_score.total} level={data.overall_score.level} size={140} />
          <p className="text-sm text-jade-400 mt-2">{data.overall_score.level}</p>
        </div>
        <div className="glass-light rounded-xl p-6 lg:col-span-2">
          <h3 className="text-sm font-medium text-white mb-4">市场机会概览</h3>
          <div className="grid gap-3 sm:grid-cols-2">
            {opportunityItems.map((item, i) => (
              <div key={i} className="rounded-lg bg-ocean-800/50 p-4">
                <p className="text-xs text-gray-500 mb-1">{item.label}</p>
                <span className="text-lg font-bold text-white">{item.value}</span>
              </div>
            ))}
          </div>
          {data.market_opportunity.source_note && (
            <p className="mt-3 text-xs leading-relaxed text-gray-500">
              {data.market_opportunity.source_note}
            </p>
          )}
        </div>
      </div>

      {/* Product Recommendations */}
      <div className="glass-light rounded-xl p-6">
        <h3 className="text-sm font-medium text-white mb-4">产品推荐</h3>
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {sortedProducts.map((product, i) => {
            const totalScore = product.scores['综合评分'] || 0;
            return (
              <div key={i} className="rounded-lg bg-ocean-800/50 p-4">
                <div className="flex items-center justify-between mb-3">
                  <span className="text-sm font-medium text-white">{product.product}</span>
                  <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${
                    totalScore >= 70 ? 'bg-jade-500/10 text-jade-400' : totalScore >= 50 ? 'bg-gold-500/10 text-gold-400' : 'bg-red-500/10 text-red-400'
                  }`}>
                    {totalScore}分
                  </span>
                </div>
                <div className="space-y-2">
                  {Object.entries(product.scores).map(([label, value]) => (
                    <div key={label}>
                      <div className="flex items-center justify-between text-xs mb-0.5">
                        <span className="text-gray-400">{label}</span>
                        <span className="text-gray-500">{value}</span>
                      </div>
                      <div className="h-1.5 rounded-full bg-ocean-900">
                        <div className="h-1.5 rounded-full bg-jade-500/70" style={{ width: `${value}%` }} />
                      </div>
                    </div>
                  ))}
                </div>
                {product.budget_fit && (
                  <p className="mt-2 text-xs text-gray-500">{product.budget_fit}</p>
                )}
                <div className="flex items-center justify-between mt-2 text-xs text-gray-500">
                  <span>MOQ: {product.suggested_moq}</span>
                  {/* 后端的 estimated_roi 是诚实文字（"净利率见利润分析"/"需采购价"），
                      不是数字——硬加 "ROI:" 前缀会把说明伪装成计算结果 */}
                  <span className="text-jade-400">{product.estimated_roi}</span>
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* Profit Analysis */}
      <div className="glass-light rounded-xl p-6">
        <h3 className="text-sm font-medium text-white mb-4">利润分析</h3>
        {data.profit_analysis.available === false && (
          <div className="mb-4 flex items-start gap-2 rounded-lg border border-gold-500/20 bg-gold-500/5 p-3">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-gold-400" />
            <div className="text-xs leading-relaxed text-gray-400">
              <span className="text-gold-400">利润分析不可用</span>：{data.profit_analysis.reason}
            </div>
          </div>
        )}
        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <p className="text-xs text-gray-500 mb-2">成本结构</p>
            <div className="space-y-2">
              {costItems.map((item, i) => (
                <div key={i} className="flex items-center justify-between text-sm">
                  <span className="text-gray-400">{item.item}</span>
                  <span className="text-white">{item.value}</span>
                </div>
              ))}
            </div>
          </div>
          <div>
            <p className="text-xs text-gray-500 mb-2">收入预估</p>
            <div className="space-y-2">
              {revenueItems.map((item, i) => (
                <div key={i} className="flex items-center justify-between text-sm">
                  <span className="text-gray-400">{item.item}</span>
                  <span className="text-white">{item.value}</span>
                </div>
              ))}
            </div>
            <div className="mt-3 pt-3 border-t border-white/5">
              <div className="flex items-center justify-between text-sm">
                <span className="text-gray-400">盈亏平衡</span>
                <span className="text-white">{data.profit_analysis.break_even.盈亏平衡销量}</span>
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* Supply Chain */}
      <div className="glass-light rounded-xl p-6">
        <h3 className="text-sm font-medium text-white mb-4 flex items-center gap-2">
          <Truck size={16} className="text-jade-400" /> 供应链推荐
        </h3>
        {data.supply_recommendations.length === 0 ? (
          <div className="flex items-start gap-2 rounded-lg border border-white/5 bg-ocean-800/40 p-3">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-gold-400" />
            <p className="text-xs leading-relaxed text-gray-400">
              <span className="text-gold-400">暂无供应商数据</span>
              {data.supply_note ? `：${data.supply_note}` : '。'}
            </p>
          </div>
        ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-gray-500 text-xs border-b border-white/5">
                <th className="pb-2 text-left font-medium">供应商</th>
                <th className="pb-2 text-left font-medium">地区</th>
                <th className="pb-2 text-left font-medium">MOQ</th>
                <th className="pb-2 text-left font-medium">价格</th>
                <th className="pb-2 text-left font-medium">评分</th>
                <th className="pb-2 text-left font-medium">推荐</th>
              </tr>
            </thead>
            <tbody>
              {data.supply_recommendations.map((s, i) => (
                <tr key={i} className="border-b border-white/5">
                  <td className="py-2 text-white">{s.supplier}</td>
                  <td className="py-2 text-gray-400">{s.location}</td>
                  <td className="py-2 text-gray-400">{s.moq}</td>
                  <td className="py-2 text-gray-400">{s.price_range}</td>
                  <td className="py-2 text-gray-400">{s.rating}</td>
                  <td className="py-2">{s.recommended ? <CheckCircle2 size={16} className="text-jade-400" /> : <Circle size={16} className="text-gray-600" />}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        )}
      </div>

      {/* Action Plan */}
      <div className="glass-light rounded-xl p-6">
        <h3 className="text-sm font-medium text-white mb-4">行动计划</h3>
        <div className="space-y-4">
          {planPhases.map((phase, i) => (
            <div key={phase.key} className="relative pl-6 border-l-2 border-jade-500/30">
              <div className="absolute -left-[7px] top-0 h-3 w-3 rounded-full bg-jade-500" />
              <div className="mb-2">
                <span className="text-xs text-jade-400">阶段 {i + 1}</span>
                <h4 className="text-sm font-medium text-white">{phase.name}</h4>
              </div>
              <div className="space-y-1.5">
                {phase.tasks.map((task, j) => (
                  <div key={j} className="flex items-center gap-2 text-sm">
                    <Circle size={14} className="text-gray-600 shrink-0" />
                    <span className="text-gray-300">{task}</span>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* 数据缺口：集中列出本页哪些字段没有数据源，避免读者把"未接入"当成"没有机会" */}
      {data.gaps && data.gaps.length > 0 && (
        <div className="rounded-xl border border-white/5 bg-ocean-800/30 p-4">
          <p className="mb-2 text-xs font-medium text-gray-400">本页的数据缺口（明确列出，不隐藏）</p>
          <ul className="space-y-1">
            {data.gaps.map((gap, i) => (
              <li key={i} className="flex items-start gap-2 text-xs text-gray-500">
                <span className="mt-1 h-1 w-1 shrink-0 rounded-full bg-gray-600" />
                {gap}
              </li>
            ))}
          </ul>
          {((data.scans_failed && data.scans_failed.length > 0) ||
            (data.scans_thin && data.scans_thin.length > 0)) && (
            <div className="mt-3 border-t border-white/5 pt-3">
              <p className="mb-1 text-xs font-medium text-gray-400">未纳入评分的候选词（逐条列出原因）</p>
              <ul className="space-y-1">
                {data.scans_failed?.map((f, i) => (
                  <li key={`f${i}`} className="text-xs text-gray-600">
                    · {f.keyword}：{f.error}
                  </li>
                ))}
                {data.scans_thin?.map((t, i) => (
                  <li key={`t${i}`} className="text-xs text-gray-600">
                    · {t.keyword}：{t.reason}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {data.provenance_ids && data.provenance_ids.length > 0 && (
            <p className="mt-3 text-xs text-gray-600">
              存证号 {data.provenance_ids.length} 条：{data.provenance_ids.slice(0, 12).map((id) => `#${id}`).join(' ')}
              {data.provenance_ids.length > 12 && ' …'}
              （每个数字可回访原始页面核对抓取时点）
            </p>
          )}
        </div>
      )}
    </motion.div>
  );
}
