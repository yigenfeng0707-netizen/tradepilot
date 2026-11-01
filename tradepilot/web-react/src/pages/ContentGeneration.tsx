import { useState, useEffect } from 'react';
import { motion } from 'framer-motion';
import { ChevronDown, Loader2, Copy, Check, Sparkles, Hash, Megaphone, FileText, RefreshCw, Info, AlertTriangle } from 'lucide-react';
import { categories } from '@/store/useStore';
import { generateContent, fetchContentOptions, type ContentGenerationData, type ContentOptions } from '@/utils/api';

function CopyBtn({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  const handleCopy = () => {
    navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };
  return (
    <button onClick={handleCopy} className="rounded p-1 text-gray-500 hover:text-jade-400 transition-colors">
      {copied ? <Check size={14} className="text-jade-400" /> : <Copy size={14} />}
    </button>
  );
}

/** 字数条：把"标题 128/200"做成可见的进度，超限变红 */
function LengthMeter({ label, len, max }: { label: string; len?: number; max?: number }) {
  if (len === undefined || max === undefined) return null;
  const pct = Math.min(100, (len / max) * 100);
  const over = len > max;
  return (
    <div>
      <div className="mb-0.5 flex items-center justify-between text-xs">
        <span className="text-gray-400">{label}</span>
        <span className={over ? 'text-red-400' : 'text-gray-500'}>{len}/{max}</span>
      </div>
      <div className="h-1.5 rounded-full bg-ocean-900">
        <div className={`h-1.5 rounded-full ${over ? 'bg-red-500/70' : 'bg-jade-500/70'}`}
          style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

export default function ContentGeneration() {
  const [productName, setProductName] = useState('自动喂食器');
  const [category, setCategory] = useState('宠物用品');
  const [platform, setPlatform] = useState('shopee');
  const [targetLang, setTargetLang] = useState('en');
  const [loading, setLoading] = useState(false);
  const [data, setData] = useState<ContentGenerationData | null>(null);
  const [error, setError] = useState('');
  // 语言/平台与其上限都从后端取，前端不硬编码——抄一份必然与后端漂移
  const [options, setOptions] = useState<ContentOptions | null>(null);

  useEffect(() => {
    fetchContentOptions().then(setOptions).catch(() => setOptions(null));
  }, []);

  const handleGenerate = async () => {
    if (!productName.trim()) return;
    setLoading(true);
    setError('');
    try {
      const result = await generateContent({
        product_name: productName,
        category,
        platform,
        target_language: targetLang,
      });
      setData(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : '生成失败，请稍后重试');
    } finally {
      setLoading(false);
    }
  };

  const languages = options?.languages ?? [
    { code: 'en', label: '英语', english: 'English', template_fallback: true },
  ];
  const platforms = options?.platforms ?? [
    { code: 'shopee', label: 'Shopee', title_max: 255, desc_max: 3000, highlight_n: 5 },
  ];
  const currentLang = languages.find((l) => l.code === targetLang);
  const currentPlatform = platforms.find((p) => p.code === platform);

  return (
    <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="space-y-6">
      {/* 输入区域 */}
      <div className="glass-light rounded-xl p-6 space-y-4">
        <h3 className="text-sm font-medium text-white flex items-center gap-2">
          <Sparkles size={16} className="text-gold-400" /> 内容生成
        </h3>
        <input
          value={productName} onChange={(e) => setProductName(e.target.value)}
          placeholder="请输入产品名称"
          className="w-full rounded-lg bg-ocean-800 px-4 py-2.5 text-sm text-white placeholder-gray-500 border border-white/10 focus:border-jade-500 focus:outline-none"
        />
        <div className="flex flex-wrap gap-4">
          <div className="relative">
            <select value={category} onChange={(e) => setCategory(e.target.value)}
              className="appearance-none rounded-lg bg-ocean-800 px-4 py-2 pr-10 text-sm text-white border border-white/10 focus:border-jade-500 focus:outline-none">
              {categories.map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
            <ChevronDown className="pointer-events-none absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" />
          </div>
          <div className="relative">
            <select value={platform} onChange={(e) => setPlatform(e.target.value)}
              className="appearance-none rounded-lg bg-ocean-800 px-4 py-2 pr-10 text-sm text-white border border-white/10 focus:border-jade-500 focus:outline-none">
              {platforms.map((p) => <option key={p.code} value={p.code}>{p.label}</option>)}
            </select>
            <ChevronDown className="pointer-events-none absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" />
          </div>
          <div className="relative">
            <select value={targetLang} onChange={(e) => setTargetLang(e.target.value)}
              className="appearance-none rounded-lg bg-ocean-800 px-4 py-2 pr-10 text-sm text-white border border-white/10 focus:border-jade-500 focus:outline-none">
              {languages.map((l) => (
                <option key={l.code} value={l.code}>
                  {l.label}{l.template_fallback ? '' : '（需 LLM）'}
                </option>
              ))}
            </select>
            <ChevronDown className="pointer-events-none absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" />
          </div>
        </div>
        {currentLang && !currentLang.template_fallback && (
          <p className="text-xs text-gold-400">
            {currentLang.label}没有模板兜底：LLM 不可用或输出未通过语言校验时会直接报错，
            不会拿其他语言的模板硬凑。
          </p>
        )}
        <button onClick={handleGenerate} disabled={loading || !productName.trim()}
          className="rounded-lg bg-jade-600 px-6 py-2.5 text-sm font-medium text-white hover:bg-jade-500 disabled:opacity-50 disabled:cursor-not-allowed transition-colors flex items-center gap-2">
          {loading ? <Loader2 size={16} className="animate-spin" /> : <Sparkles size={16} />}
          {loading ? '生成中...' : '生成内容'}
        </button>
      </div>

      {error && (
        <div className="rounded-lg bg-red-500/10 border border-red-500/20 p-4 flex items-center justify-between">
          <span className="text-sm text-red-400">{error}</span>
          <button onClick={handleGenerate} disabled={loading}
            className="rounded-lg bg-red-500/20 px-3 py-1.5 text-xs text-red-400 hover:bg-red-500/30 transition-colors flex items-center gap-1">
            <RefreshCw size={12} /> 重试
          </button>
        </div>
      )}

      {loading && (
        <div className="flex h-48 flex-col items-center justify-center gap-2">
          <div className="flex items-center">
            <Loader2 className="h-8 w-8 animate-spin text-jade-500" />
            <span className="ml-3 text-gray-400">AI 正在生成内容...</span>
          </div>
          <span className="text-xs text-gray-600">
            单次生成约 60–90 秒（含真实竞品抓取）；失败会自动退回模板骨架，不会一直转圈
          </span>
        </div>
      )}

      {data && !loading && (
        <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} className="space-y-6">
          {/* 口径提示：这是文案不是数据，且平台上限是配置常量 */}
          <div className="flex items-start gap-2 rounded-lg border border-jade-500/20 bg-jade-500/5 p-3">
            <Info className="mt-0.5 h-4 w-4 shrink-0 text-jade-400" />
            <div className="text-xs leading-relaxed text-gray-400">
              <span className="text-jade-400">生成内容</span>
              {' · '}
              {data.platform_label} / {data.language_label} · 来源：{data.generated_by}
              <span className="block mt-1 text-gray-500">
                文案为模型/模板生成，不是可溯源数据；其中 [ADD] 处必须由卖家补齐真实规格后才能上架。
                SEO 关键词是表达建议，不含搜索量依据。
              </span>
              {data.llm_failure_reason && (
                <span className="block mt-1 text-gold-400">
                  LLM 未返回合格内容，已退回模板骨架：{data.llm_failure_reason}
                </span>
              )}
              {data.platform_compliance.rules_source && (
                <span className="block mt-1 text-gray-600">
                  平台字数上限：{data.platform_compliance.rules_source}
                </span>
              )}
            </div>
          </div>

          {/* 字数校验：把上限做成可见的进度条 */}
          <div className="glass-light rounded-xl p-6 space-y-3">
            <h4 className="text-sm font-medium text-white">平台字数校验</h4>
            <LengthMeter label="标题长度" len={data.platform_compliance.title_len}
              max={data.platform_compliance.title_max} />
            <LengthMeter label="描述长度" len={data.platform_compliance.desc_len}
              max={data.platform_compliance.desc_max} />
            {data.market_facts && (
              <p className="text-xs text-gray-500">
                定位参考：真实竞品 {data.market_facts.competitor_n} 条（{data.market_facts.source}，
                {data.market_facts.currency} 价格带 {data.market_facts.price_band?.min}–
                {data.market_facts.price_band?.max}），仅用于措辞定位，不是本品价格
              </p>
            )}
          </div>

          {/* 产品标题 */}
          <div className="glass-light rounded-xl p-6">
            <div className="flex items-center justify-between mb-2">
              <h4 className="text-sm font-medium text-white flex items-center gap-2"><FileText size={14} className="text-jade-400" /> 产品标题</h4>
              <CopyBtn text={data.content.title} />
            </div>
            <p className="text-sm text-gray-300">{data.content.title}</p>
          </div>

          {/* 产品描述 */}
          <div className="glass-light rounded-xl p-6">
            <div className="flex items-center justify-between mb-2">
              <h4 className="text-sm font-medium text-white flex items-center gap-2"><FileText size={14} className="text-jade-400" /> 产品描述</h4>
              <CopyBtn text={data.content.description} />
            </div>
            <p className="text-sm text-gray-300 whitespace-pre-wrap">{data.content.description}</p>
          </div>

          {/* 卖点 */}
          <div className="glass-light rounded-xl p-6">
            <h4 className="text-sm font-medium text-white mb-3">核心卖点</h4>
            <div className="space-y-2">
              {data.content.highlights.map((h, i) => (
                <div key={i} className="flex items-center gap-2 rounded-lg bg-ocean-800/50 p-3">
                  <span className="text-sm">{h.icon}</span>
                  <span className="text-sm text-gray-300">{h.text}</span>
                </div>
              ))}
            </div>
          </div>

          {/* SEO 关键词 */}
          <div className="glass-light rounded-xl p-6">
            <h4 className="text-sm font-medium text-white mb-3 flex items-center gap-2"><Hash size={14} className="text-gold-400" /> SEO 关键词</h4>
            <div className="flex flex-wrap gap-2">
              {data.content.seo_keywords.map((kw, i) => (
                <span key={i} className="rounded-full bg-gold-500/10 px-3 py-1 text-xs text-gold-400">{kw}</span>
              ))}
            </div>
          </div>

          {/* 社媒文案 & 广告文案 */}
          <div className="grid gap-6 lg:grid-cols-2">
            <div className="glass-light rounded-xl p-6">
              <h4 className="text-sm font-medium text-white mb-3 flex items-center gap-2"><Megaphone size={14} className="text-jade-400" /> 社媒文案</h4>
              <div className="space-y-2 text-sm">
                <p className="text-gray-400">钩子: <span className="text-gray-300">{data.marketing.social_copy.hook}</span></p>
                <p className="text-gray-400">痛点: <span className="text-gray-300">{data.marketing.social_copy.pain_point}</span></p>
                <p className="text-gray-400">方案: <span className="text-gray-300">{data.marketing.social_copy.solution}</span></p>
                <p className="text-gray-400">行动: <span className="text-gray-300">{data.marketing.social_copy.cta}</span></p>
                <div className="flex flex-wrap gap-1 pt-1">
                  {data.marketing.social_copy.hashtags.map((tag, i) => (
                    <span key={i} className="text-xs text-jade-400">{tag}</span>
                  ))}
                </div>
              </div>
            </div>
            <div className="glass-light rounded-xl p-6">
              <h4 className="text-sm font-medium text-white mb-3 flex items-center gap-2"><Megaphone size={14} className="text-gold-400" /> 广告文案</h4>
              <div className="space-y-2 text-sm">
                <p className="text-gray-400">标题: <span className="text-white font-medium">{data.marketing.ad_copy.headline}</span></p>
                <p className="text-gray-400">正文: <span className="text-gray-300">{data.marketing.ad_copy.body}</span></p>
                <p className="text-gray-400">按钮: <span className="rounded bg-gold-500/20 px-2 py-0.5 text-gold-400">{data.marketing.ad_copy.cta_button}</span></p>
              </div>
            </div>
          </div>

          {/* 合规提示 */}
          {data.platform_compliance.warnings.length > 0 && (
            <div className="rounded-lg border border-gold-500/20 bg-gold-500/5 p-4">
              <p className="text-xs text-gold-400 mb-1">平台合规提示</p>
              {data.platform_compliance.warnings.map((w, i) => (
                <p key={i} className="text-xs text-gray-400">• {w}</p>
              ))}
            </div>
          )}

          {/* 数据缺口：这份文案哪些地方没有依据，集中说清 */}
          {data.gaps && data.gaps.length > 0 && (
            <div className="rounded-xl border border-white/5 bg-ocean-800/30 p-4">
              <p className="mb-2 flex items-center gap-1.5 text-xs font-medium text-gray-400">
                <AlertTriangle size={12} className="text-gold-400" />
                这份文案的边界（明确列出，不隐藏）
              </p>
              <ul className="space-y-1">
                {data.gaps.map((gap, i) => (
                  <li key={i} className="flex items-start gap-2 text-xs text-gray-500">
                    <span className="mt-1 h-1 w-1 shrink-0 rounded-full bg-gray-600" />
                    {gap}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </motion.div>
      )}
    </motion.div>
  );
}
