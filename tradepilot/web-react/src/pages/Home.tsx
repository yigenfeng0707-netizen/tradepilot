import { useEffect, useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { useNavigate } from 'react-router-dom';
import {
  ArrowRight,
  BarChart3,
  Target,
  FileText,
  Shield,
  Headphones,
  PawPrint,
  Home as HomeIcon,
  Sparkles,
  Cpu,
  Shirt,
  Baby,
  Mountain,
  Car,
  Cookie,
  PenTool,
  X,
} from 'lucide-react';
import CategoryCard from '@/components/CategoryCard';
import { useStore } from '@/store/useStore';
import { fetchCapabilities } from '@/utils/api';

const agents = [
  { name: '市场洞察', icon: BarChart3, online: true, route: '/market-insight' },
  { name: '智能选品', icon: Target, online: true, route: '/smart-selection' },
  { name: '跨境内容生成', icon: FileText, online: true, route: '/content-generation' },
  { name: '合规助手', icon: Shield, online: true, route: '/compliance' },
  { name: '智能客服', icon: Headphones, online: true, route: '/customer-service' },
];

const categoryData = [
  { icon: PawPrint, name: '宠物用品', description: '宠物食品、玩具、护理用品', },
  { icon: HomeIcon, name: '家居用品', description: '家居装饰、收纳、清洁用品', },
  { icon: Sparkles, name: '美妆个护', description: '护肤、彩妆、个人护理', },
  { icon: Cpu, name: '3C电子', description: '消费电子、智能设备、配件', },
  { icon: Shirt, name: '服装服饰', description: '女装、男装、童装、配饰', },
  { icon: Baby, name: '母婴用品', description: '婴儿护理、孕产用品、玩具', },
  { icon: Mountain, name: '户外运动', description: '运动装备、户外用品、健身', },
  { icon: Car, name: '汽车配件', description: '车载电子、内饰、保养用品', },
  { icon: Cookie, name: '食品零食', description: '休闲零食、健康食品、饮品', },
  { icon: PenTool, name: '办公文具', description: '办公用品、文具、创意产品', },
];

/** 这些数字以前是写死的（50+ 覆盖市场 / 8 支持语言 / 5 合规国家），
 *  全是编的：真实可抓市场只有 US，语言 9 种，合规国家 7 个。
 *  现在全部来自 /api/capabilities，页面写"可溯源"就不能在这里摆假数字。 */
interface Capabilities {
  categories: number;
  languages: number;
  platforms: number;
  compliance_countries: number;
  compliance_official_source_countries: number;
  categories_with_builtin_keywords: string[];
}

const container = {
  hidden: { opacity: 0 },
  show: { opacity: 1, transition: { staggerChildren: 0.08 } },
};

const item = {
  hidden: { opacity: 0, y: 20 },
  show: { opacity: 1, y: 0 },
};

export default function Home() {
  const navigate = useNavigate();
  const { setSelectedCategory } = useStore();
  const [showGuide, setShowGuide] = useState(() => !localStorage.getItem('haisi-guide-seen'));
  const [caps, setCaps] = useState<Capabilities | null>(null);

  useEffect(() => {
    fetchCapabilities().then(setCaps).catch(() => setCaps(null));
  }, []);

  // 拿不到就不显示数字，而不是回落一组写死的值
  const statCards = caps
    ? [
        { icon: BarChart3, value: String(caps.categories), label: '可直接跑数据的品类' },
        { icon: Sparkles, value: String(caps.languages), label: '支持语言' },
        { icon: FileText, value: String(caps.platforms), label: '上架平台' },
        { icon: Shield, value: String(caps.compliance_countries), label: '合规国家' },
      ]
    : [];

  const handleCloseGuide = () => {
    setShowGuide(false);
    localStorage.setItem('haisi-guide-seen', '1');
  };

  const handleCategoryClick = (name: string) => {
    setSelectedCategory(name);
    navigate(`/market-insight?category=${encodeURIComponent(name)}`);
  };

  const guideSteps = [
    { num: 1, title: '选择品类', desc: '从常用跨境品类中选择' },
    { num: 2, title: '启动Agent', desc: '点击任意智能体卡片进入功能页面' },
    { num: 3, title: '一键出海', desc: '使用全链路工作流，4 步完成选品到合规' },
  ];

  return (
    <div className="space-y-8">
      {/* First-visit Guide Overlay */}
      <AnimatePresence>
        {showGuide && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.3 }}
            className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
            onClick={handleCloseGuide}
          >
            <motion.div
              initial={{ opacity: 0, scale: 0.9 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={{ opacity: 0, scale: 0.9 }}
              transition={{ duration: 0.35, ease: 'easeOut' }}
              onClick={(e) => e.stopPropagation()}
              className="glass-light relative mx-4 w-full max-w-md rounded-2xl p-8 text-center"
            >
              <button
                onClick={handleCloseGuide}
                className="absolute right-4 top-4 text-gray-500 hover:text-white transition-colors"
              >
                <X size={18} />
              </button>
              <h2 className="font-display text-2xl text-white mb-2">
                欢迎使用<span className="text-gold-500">TradePilot</span> <span className="text-jade-500">AI Agent</span>
              </h2>
              <p className="text-sm text-gray-400 mb-6">3步快速上手，开启跨境出海之旅</p>
              <div className="space-y-4 mb-8">
                {guideSteps.map((step) => (
                  <div key={step.num} className="flex items-start gap-4 rounded-xl bg-ocean-800/50 p-4 text-left">
                    <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-gold-500 to-jade-500 text-sm font-bold text-ocean-900">
                      {step.num}
                    </div>
                    <div>
                      <p className="text-sm font-medium text-white">{step.title}</p>
                      <p className="text-xs text-gray-400 mt-0.5">{step.desc}</p>
                    </div>
                  </div>
                ))}
              </div>
              <button
                onClick={handleCloseGuide}
                className="w-full rounded-lg bg-gradient-to-r from-gold-500 to-jade-500 px-6 py-3 text-sm font-bold text-ocean-900 transition-opacity hover:opacity-90"
              >
                开始体验
              </button>
              <p className="mt-4 text-xs text-gray-500">TradePilot · 数据可溯源的跨境电商智能助手</p>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
      {/* Hero Section */}
      <section className="relative overflow-hidden rounded-2xl bg-gradient-to-br from-ocean-900 via-ocean-800 to-ocean-900 p-8 lg:p-12">
        <div className="absolute inset-0 bg-[radial-gradient(circle_at_30%_50%,rgba(0,201,167,0.1),transparent_50%)]" />
        <div className="absolute inset-0 bg-[radial-gradient(circle_at_70%_80%,rgba(212,168,83,0.08),transparent_50%)]" />
        <motion.div
          initial={{ opacity: 0, y: 30 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.6 }}
          className="relative z-10"
        >
          <h1 className="font-display text-4xl lg:text-5xl mb-3">
            <span className="text-gold-500">TradePilot</span>{' '}
            <span className="text-jade-500">AI Agent</span>
          </h1>
          <p className="text-lg text-gray-300 max-w-2xl mb-6">
            每个数字都带来源存证：市场洞察、智能选品、合规核查、内容生成、智能客服一站式打通
          </p>
          <button
            onClick={() => navigate('/market-insight')}
            className="inline-flex items-center gap-2 rounded-lg bg-jade-500 px-6 py-3 text-sm font-medium text-ocean-900 transition-colors hover:bg-jade-400"
          >
            开始探索 <ArrowRight size={16} />
          </button>
        </motion.div>
      </section>

      {/* 能力声明：数字来自 /api/capabilities，不是市场数据 */}
      <section>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          {statCards.map((stat) => (
            <motion.div
              key={stat.label}
              variants={item}
              initial="hidden"
              animate="show"
              className="glass-light rounded-xl p-5 flex flex-col items-center justify-center text-center"
            >
              <stat.icon size={24} className="text-gold-400 mb-2" />
              <div className="text-3xl font-bold text-white">{stat.value}</div>
              <div className="text-xs text-gray-400 mt-1">{stat.label}</div>
            </motion.div>
          ))}
        </div>
        <p className="mt-2 text-xs text-gray-600">
          以上为能力声明（能做什么），不是市场规模或增长率——那些数字需要真实数据源，
          没有就如实写「未接入」。
        </p>
      </section>

      {/* Agent Status */}
      <section>
        <h2 className="text-lg font-medium text-white mb-4">智能体状态</h2>
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3">
          {agents.map((agent) => (
            <motion.div
              key={agent.name}
              variants={item}
              initial="hidden"
              animate="show"
              onClick={() => navigate(agent.route)}
              className="glass-light rounded-xl p-4 flex items-center gap-3 cursor-pointer hover:border-gold-500/30 hover:bg-white/[0.04] transition-colors"
            >
              <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-ocean-800">
                <agent.icon size={20} className="text-gold-400" />
              </div>
              <div>
                <div className="text-sm font-medium text-white">{agent.name}</div>
                <div className="flex items-center gap-1 mt-0.5">
                  <span
                    className={`h-2 w-2 rounded-full ${
                      agent.online ? 'bg-jade-500' : 'bg-gold-500'
                    }`}
                  />
                  <span className="text-xs text-gray-500">
                    {agent.online ? '在线' : '开发中'}
                  </span>
                </div>
              </div>
            </motion.div>
          ))}
        </div>
      </section>

      {/* Category Navigation */}
      <section>
        <h2 className="text-lg font-medium text-white mb-4">品类导航</h2>
        <motion.div
          variants={container}
          initial="hidden"
          animate="show"
          className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3"
        >
          {categoryData.map((cat) => (
            <motion.div key={cat.name} variants={item}>
              <div onClick={() => handleCategoryClick(cat.name)}>
                <CategoryCard
                  icon={cat.icon}
                  name={cat.name}
                  description={cat.description}
                  hasRealSource={
                    caps
                      ? caps.categories_with_builtin_keywords.includes(cat.name)
                      : undefined
                  }
                />
              </div>
            </motion.div>
          ))}
        </motion.div>
      </section>
    </div>
  );
}
