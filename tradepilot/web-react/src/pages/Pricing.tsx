import { useEffect, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { motion, AnimatePresence } from 'framer-motion';
import { Check, ChevronDown, ChevronUp, Sparkles, X } from 'lucide-react';
import {
  BillingOrder, createBillingOrder, fetchBillingOrders, syncBillingOrder,
} from '../utils/api';

interface Plan {
  name: string;
  price: string;
  period: string;
  description: string;
  features: string[];
  highlight: boolean;
  cta: string;
}

/** 数字口径与 GET /api/capabilities 对齐（可跑真实数据的品类 4、语言 9、
 *  合规国家 7）。价格由运营于 2026-11-20 依据市场对标拍板（见
 *  config/pricing.json）：内测免费、正式版 ¥199/月（对标 Shopify Basic 年付价）、
 *  私有化面议。计费系统上线前此价为公示要约，不做虚假折扣。 */
const plans: Plan[] = [
  {
    name: '内测版',
    price: '免费',
    period: '（内测期）',
    description: '内测期间全部功能开放，正式定价未定',
    features: ['市场洞察报告（真实抓取带存证）', '智能选品推荐', '跨境内容生成（9 语种 × 多平台）', '合规查询（7 国）', '全链路编排'],
    highlight: true,
    cta: '注册即用',
  },
  {
    name: '正式版',
    price: '¥199',
    period: '/月',
    description: '对标 Shopify Basic 年付价；日配额 2000 点，token 用量透明可查',
    features: ['内测版全部功能', '日配额 2000 点（默认 20 倍）', '多席位与租户协作', 'API 接口对接'],
    highlight: false,
    cta: '加入等待名单',
  },
  {
    name: '私有化部署',
    price: '面议',
    period: '',
    description: '数据不出域的独立部署',
    features: ['完整平台能力', '私有化部署（Docker）', '定制 Agent 开发', '专属支持'],
    highlight: false,
    cta: '联系我们',
  },
];

const comparisonFeatures = [
  { name: '市场洞察报告（真实抓取）', basic: true, pro: true, enterprise: true },
  { name: '智能选品推荐', basic: true, pro: true, enterprise: true },
  { name: '跨境内容生成', basic: '9 语种', pro: '9 语种', enterprise: '9 语种 + 定制' },
  { name: '合规查询', basic: '7 国', pro: '7 国', enterprise: '7 国 + 定制' },
  { name: '智能客服', basic: true, pro: true, enterprise: true },
  { name: '用量明细（token 级）', basic: true, pro: true, enterprise: true },
  { name: '私有化部署', basic: false, pro: false, enterprise: true },
  { name: 'API 接口', basic: false, pro: true, enterprise: true },
];

const faqs = [
  { q: '现在收费吗？', a: '内测期免费开放全部功能。正式版 ¥199/月（对标 Shopify Basic 年付价，2026-11-20 公示），日配额 2000 点；每次 LLM 调用的输入/输出 token 都有明细，用量透明可查。' },
  { q: '用量怎么算？', a: '按请求权重扣减每日配额（全链路 4 点、文案 2 点、单 Agent 1 点）；同时每次 LLM 调用都有 token 级明细。两套口径一个是界面余额，一个是成本账单。' },
  { q: '数据是真实的吗？', a: '市场数据来自真实抓取并带来源存证，可逐条回访；抓不到的字段显式标注「未接入」，不编造。合规条款逐条标注验证状态（已定位原文 / 仅取得页面 / 未验证）。' },
];

const fadeUp = { hidden: { opacity: 0, y: 20 }, show: { opacity: 1, y: 0 } };

export default function Pricing() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const [openFaq, setOpenFaq] = useState<number | null>(null);
  const [showContactModal, setShowContactModal] = useState(false);
  const [submitSuccess, setSubmitSuccess] = useState(false);
  const [contactForm, setContactForm] = useState({
    company: '',
    email: '',
    phone: '',
  });
  const [orders, setOrders] = useState<BillingOrder[]>([]);
  const [paying, setPaying] = useState(false);
  const [payError, setPayError] = useState<string | null>(null);
  const [syncing, setSyncing] = useState<string | null>(null);

  const loggedIn = Boolean(localStorage.getItem('tp_access_token'));

  const loadOrders = async () => {
    if (!loggedIn) return;
    try { setOrders(await fetchBillingOrders()); } catch { /* 订单列表失败不阻塞页面 */ }
  };

  useEffect(() => { loadOrders(); }, [loggedIn]);

  // 同步回跳：支付宝付完跳回 /pricing?order=OUT_TRADE_NO，主动对账兜底
  // （异步通知可能晚于回跳——真相以 trade.query 为准）
  useEffect(() => {
    const no = searchParams.get('order');
    if (!no || !loggedIn) return;
    setSyncing(no);
    syncBillingOrder(no)
      .then(() => loadOrders())
      .catch(() => undefined)
      .finally(() => setSyncing(null));
  }, [searchParams, loggedIn]);

  const handleCtaClick = async (planName: string) => {
    if (planName === '私有化部署') {
      setShowContactModal(true);
      setSubmitSuccess(false);
      return;
    }
    if (!loggedIn) { navigate('/login'); return; }
    if (planName === '内测版') { navigate('/app'); return; }
    // 正式版：下单 → 跳支付宝官方收银台（page.pay 签名链接由后端生成）
    setPaying(true); setPayError(null);
    try {
      const order = await createBillingOrder('pro');
      window.location.href = order.pay_url as string;
    } catch (e) {
      setPayError(e instanceof Error ? e.message : '下单失败');
      setPaying(false);
    }
  };

  const handleSync = async (no: string) => {
    setSyncing(no);
    try { await syncBillingOrder(no); } catch { /* 查询失败保留现状 */ }
    finally { setSyncing(null); loadOrders(); }
  };

  const handleContactSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setSubmitSuccess(true);
  };

  const handleCloseModal = () => {
    setShowContactModal(false);
    if (submitSuccess) {
      setContactForm({ company: '', email: '', phone: '' });
    }
  };

  return (
    <div className="space-y-12">
      {/* Header */}
      <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} className="text-center">
        <h1 className="font-display text-3xl text-white mb-2">套餐价格</h1>
        <p className="text-gray-400">选择适合您的方案，开启跨境电商智能之旅</p>
      </motion.div>

      {/* Pricing Cards */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-6 items-start">
        {plans.map((plan, i) => (
          <motion.div
            key={plan.name}
            variants={fadeUp}
            initial="hidden"
            animate="show"
            transition={{ delay: i * 0.1 }}
            className={`relative rounded-2xl p-6 ${
              plan.highlight
                ? 'bg-gradient-to-b from-gold-500/10 to-ocean-900 border-2 border-gold-500/50 md:scale-105 md:py-8'
                : 'glass-light border border-white/5'
            }`}
          >
            {plan.highlight && (
              <span className="absolute -top-3 left-1/2 -translate-x-1/2 rounded-full bg-gradient-to-r from-gold-500 to-gold-400 px-4 py-1 text-xs font-bold text-ocean-900">
                推荐
              </span>
            )}
            <h3 className="text-lg font-medium text-white">{plan.name}</h3>
            <div className="mt-3 flex items-baseline gap-1">
              <span className="text-4xl font-bold text-white">{plan.price}</span>
              <span className="text-sm text-gray-400">{plan.period}</span>
            </div>
            <p className="mt-2 text-sm text-gray-400">{plan.description}</p>
            <ul className="mt-6 space-y-3">
              {plan.features.map((f) => (
                <li key={f} className="flex items-center gap-2 text-sm text-gray-300">
                  <Check size={16} className="shrink-0 text-jade-500" />
                  {f}
                </li>
              ))}
            </ul>
            <button
              className={`mt-8 w-full rounded-lg py-2.5 text-sm font-medium transition-colors ${
                plan.highlight
                  ? 'bg-gradient-to-r from-gold-500 to-gold-400 text-ocean-900 hover:from-gold-400 hover:to-gold-300'
                  : plan.name === '私有化部署'
                  ? 'bg-ocean-800 text-white hover:bg-ocean-700'
                  : 'bg-jade-500 text-ocean-900 hover:bg-jade-400'
              }`}
              onClick={() => handleCtaClick(plan.name)}
              disabled={paying}
            >
              {plan.highlight && <Sparkles size={14} className="inline mr-1" />}
              {plan.name === '正式版' && paying ? '正在创建订单…' : plan.cta}
            </button>
            {plan.name === '正式版' && payError && (
              <p className="mt-2 text-xs text-red-400">{payError}</p>
            )}
            {plan.name === '正式版' && orders.some((o) => o.status === 'PAID' && !o.test) && (
              <p className="mt-2 text-xs text-jade-400">已订阅正式版，配额已升级至 2000 点/日。</p>
            )}
          </motion.div>
        ))}
      </div>

      {/* 我的订单（登录后可见）——状态如实展示，自测单明确标注 */}
      {loggedIn && orders.length > 0 && (
        <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }}>
          <h2 className="text-xl font-medium text-white mb-4">我的订单</h2>
          <div className="overflow-x-auto rounded-xl border border-white/5">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-white/5 bg-ocean-950/50">
                  <th className="px-4 py-3 text-left text-gray-400 font-medium">订单号</th>
                  <th className="px-4 py-3 text-left text-gray-400 font-medium">商品</th>
                  <th className="px-4 py-3 text-right text-gray-400 font-medium">金额</th>
                  <th className="px-4 py-3 text-center text-gray-400 font-medium">状态</th>
                  <th className="px-4 py-3 text-right text-gray-400 font-medium">操作</th>
                </tr>
              </thead>
              <tbody>
                {orders.map((o) => (
                  <tr key={o.out_trade_no} className="border-b border-white/5">
                    <td className="px-4 py-3 text-gray-400 font-mono text-xs">{o.out_trade_no}</td>
                    <td className="px-4 py-3 text-gray-300">
                      {o.subject}
                      {o.test && (
                        <span className="ml-2 rounded bg-gold-500/20 px-1.5 py-0.5 text-xs text-gold-400">
                          支付链路自测单
                        </span>
                      )}
                    </td>
                    <td className="px-4 py-3 text-right text-gray-300">¥{o.amount.toFixed(2)}</td>
                    <td className="px-4 py-3 text-center">
                      {o.status === 'PAID' ? (
                        <span className="rounded bg-jade-500/20 px-2 py-0.5 text-xs text-jade-400">已支付</span>
                      ) : (
                        <span className="rounded bg-white/10 px-2 py-0.5 text-xs text-gray-400">待支付</span>
                      )}
                    </td>
                    <td className="px-4 py-3 text-right">
                      {o.status !== 'PAID' && (
                        <button
                          onClick={() => handleSync(o.out_trade_no)}
                          disabled={syncing === o.out_trade_no}
                          className="rounded bg-ocean-800 px-3 py-1 text-xs text-white hover:bg-ocean-700 disabled:opacity-50"
                        >
                          {syncing === o.out_trade_no ? '对账中…' : '查询支付结果'}
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </motion.div>
      )}

      {/* Comparison Table */}
      <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: 0.4 }}>
        <h2 className="text-xl font-medium text-white mb-4">功能对比</h2>
        <div className="overflow-x-auto rounded-xl border border-white/5">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-white/5 bg-ocean-950/50">
                <th className="px-4 py-3 text-left text-gray-400 font-medium">功能</th>
                <th className="px-4 py-3 text-center text-gray-400 font-medium">内测版</th>
                <th className="px-4 py-3 text-center text-gold-400 font-medium">正式版</th>
                <th className="px-4 py-3 text-center text-gray-400 font-medium">私有化部署</th>
              </tr>
            </thead>
            <tbody>
              {comparisonFeatures.map((row) => (
                <tr key={row.name} className="border-b border-white/5">
                  <td className="px-4 py-3 text-gray-300">{row.name}</td>
                  {([row.basic, row.pro, row.enterprise] as (boolean | string)[]).map((val, j) => (
                    <td key={j} className="px-4 py-3 text-center">
                      {val === true ? (
                        <Check size={16} className="mx-auto text-jade-500" />
                      ) : val === false ? (
                        <span className="text-gray-600">—</span>
                      ) : (
                        <span className="text-gray-300">{val}</span>
                      )}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </motion.div>

      {/* FAQ */}
      <div>
        <h2 className="text-xl font-medium text-white mb-4">常见问题</h2>
        <div className="space-y-3">
          {faqs.map((faq, i) => (
            <motion.div
              key={i}
              variants={fadeUp}
              initial="hidden"
              animate="show"
              transition={{ delay: 0.5 + i * 0.08 }}
              className="rounded-xl border border-white/5 bg-ocean-950/50"
            >
              <button
                className="flex w-full items-center justify-between px-5 py-4 text-left text-sm text-white"
                onClick={() => setOpenFaq(openFaq === i ? null : i)}
              >
                {faq.q}
                {openFaq === i ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
              </button>
              {openFaq === i && (
                <div className="px-5 pb-4 text-sm text-gray-400">{faq.a}</div>
              )}
            </motion.div>
          ))}
        </div>
      </div>

      {/* Contact Sales Modal */}
      <AnimatePresence>
        {showContactModal && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
            onClick={handleCloseModal}
          >
            <motion.div
              initial={{ opacity: 0, scale: 0.9, y: 20 }}
              animate={{ opacity: 1, scale: 1, y: 0 }}
              exit={{ opacity: 0, scale: 0.9, y: 20 }}
              className="relative w-full max-w-md rounded-2xl border border-white/10 bg-ocean-900 p-6 shadow-2xl"
              onClick={(e) => e.stopPropagation()}
            >
              <button
                onClick={handleCloseModal}
                className="absolute right-4 top-4 text-gray-400 hover:text-white transition-colors"
              >
                <X size={20} />
              </button>

              {submitSuccess ? (
                <div className="text-center py-6">
                  <div className="mx-auto mb-4 flex h-16 w-16 items-center justify-center rounded-full bg-jade-500/20">
                    <Check size={32} className="text-jade-500" />
                  </div>
                  <h3 className="text-xl font-medium text-white mb-2">提交成功</h3>
                  <p className="text-gray-400">我们将在24小时内联系您</p>
                  <button
                    onClick={handleCloseModal}
                    className="mt-6 rounded-lg bg-jade-500 px-6 py-2.5 text-sm font-medium text-ocean-900 transition-colors hover:bg-jade-400"
                  >
                    知道了
                  </button>
                </div>
              ) : (
                <>
                  <h3 className="text-lg font-medium text-white mb-1">联系销售</h3>
                  <p className="text-sm text-gray-400 mb-6">填写以下信息，我们的客户经理将与您联系</p>
                  <form onSubmit={handleContactSubmit} className="space-y-4">
                    <div>
                      <label className="block text-sm text-gray-300 mb-1.5">公司名称</label>
                      <input
                        type="text"
                        required
                        value={contactForm.company}
                        onChange={(e) => setContactForm({ ...contactForm, company: e.target.value })}
                        className="w-full rounded-lg border border-white/10 bg-ocean-800 px-4 py-2.5 text-sm text-white placeholder-gray-500 outline-none focus:border-gold-500/50 transition-colors"
                        placeholder="请输入公司名称"
                      />
                    </div>
                    <div>
                      <label className="block text-sm text-gray-300 mb-1.5">邮箱</label>
                      <input
                        type="email"
                        required
                        value={contactForm.email}
                        onChange={(e) => setContactForm({ ...contactForm, email: e.target.value })}
                        className="w-full rounded-lg border border-white/10 bg-ocean-800 px-4 py-2.5 text-sm text-white placeholder-gray-500 outline-none focus:border-gold-500/50 transition-colors"
                        placeholder="请输入邮箱地址"
                      />
                    </div>
                    <div>
                      <label className="block text-sm text-gray-300 mb-1.5">联系电话</label>
                      <input
                        type="tel"
                        required
                        value={contactForm.phone}
                        onChange={(e) => setContactForm({ ...contactForm, phone: e.target.value })}
                        className="w-full rounded-lg border border-white/10 bg-ocean-800 px-4 py-2.5 text-sm text-white placeholder-gray-500 outline-none focus:border-gold-500/50 transition-colors"
                        placeholder="请输入联系电话"
                      />
                    </div>
                    <button
                      type="submit"
                      className="w-full rounded-lg bg-gradient-to-r from-gold-500 to-gold-400 py-2.5 text-sm font-medium text-ocean-900 transition-colors hover:from-gold-400 hover:to-gold-300"
                    >
                      提交
                    </button>
                  </form>
                </>
              )}
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
