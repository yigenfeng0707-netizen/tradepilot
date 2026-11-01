import { useState, useEffect } from 'react';
import { Outlet, NavLink, useLocation, useNavigate } from 'react-router-dom';
import { Home, BarChart3, Target, Menu, X, Database, Sparkles, ShieldCheck, MessageCircle, GitMerge, CreditCard, LogOut, User, MonitorPlay, Settings, Plug, Gauge } from 'lucide-react';
import { clearSession, fetchMe, fetchSystemStatus, hasSession, logoutUser, type SystemStatus } from '@/utils/api';
import { motion, AnimatePresence } from 'framer-motion';
import { useStore } from '@/store/useStore';

const navItems = [
  { to: '/', icon: Home, label: '首页' },
  { to: '/market-insight', icon: BarChart3, label: '市场洞察' },
  { to: '/smart-selection', icon: Target, label: '智能选品' },
  { to: '/content-generation', icon: Sparkles, label: '内容生成' },
  { to: '/compliance', icon: ShieldCheck, label: '合规查询' },
  { to: '/customer-service', icon: MessageCircle, label: '智能客服' },
  { to: '/integrations', icon: Plug, label: '店铺集成' },
  { to: '/account', icon: Gauge, label: '我的用量' },
  { to: '/pipeline', icon: GitMerge, label: '全链路' },
  { to: '/pricing', icon: CreditCard, label: '套餐价格' },
  { to: '/admin', icon: Settings, label: '运营后台' },
];

/** 大屏是独立的 M1 原生 Canvas 页面（/canvas），不属 React 路由。
 *  用外链而不是 NavLink：它由后端直接吐 HTML，切进去整页重载。 */
const canvasLink = { href: '/canvas', icon: MonitorPlay, label: '运营大屏' };

export default function Layout() {
  const [sidebarOpen, setSidebarOpen] = useState(false);
  // 拿不到状态就显示"未接入"——绝不填一批假的 online 上去。
  // 这个面板本身就是给人看"哪些是通的"，造假比不显示更糟。
  const [sys, setSys] = useState<SystemStatus | null>(null);
  const [sysError, setSysError] = useState(false);
  const location = useLocation();
  const navigate = useNavigate();
  const { isAuthenticated, user, login, logout } = useStore();

  useEffect(() => {
    fetchSystemStatus()
      .then((s) => { setSys(s); setSysError(false); })
      .catch(() => { setSys(null); setSysError(true); });
  }, []);

  // 恢复会话：令牌存 localStorage，但 zustand 是内存态，刷新即空。
  // 用 /auth/me 校验令牌并取回用户信息；令牌失效就清掉，让用户重新登录。
  // **只在挂载时跑一次**：监听 isAuthenticated 会跟登出竞态——
  // logoutUser() 还没清完 localStorage，这里就会拿旧令牌把用户重新登回来。
  useEffect(() => {
    if (isAuthenticated || !hasSession()) return;
    fetchMe()
      .then((u) => login({ id: u.user_id, email: u.email,
                           company: u.company || '', plan: 'free' }))
      .catch(() => clearSession());
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    setSidebarOpen(false);
  }, [location.pathname]);

  return (
    <div className="flex h-screen overflow-hidden bg-ocean-900">
      {/* Mobile overlay */}
      <AnimatePresence>
        {sidebarOpen && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="fixed inset-0 z-30 bg-black/50 lg:hidden"
            onClick={() => setSidebarOpen(false)}
          />
        )}
      </AnimatePresence>

      {/* Sidebar */}
      <aside
        className={`fixed z-40 flex h-full w-64 flex-col bg-ocean-950 border-r border-white/5 transition-transform duration-300 lg:static lg:translate-x-0 ${
          sidebarOpen ? 'translate-x-0' : '-translate-x-full'
        }`}
      >
        {/* Logo */}
        <div className="flex items-center gap-2 px-6 py-5 border-b border-white/5">
          <div className="flex items-baseline gap-1">
            <span className="font-display text-2xl text-gold-500">TradePilot</span>
            <span className="font-display text-lg text-jade-500">AI Agent</span>
          </div>
        </div>

        {/* Navigation */}
        <nav className="flex-1 px-3 py-4 space-y-1">
          {navItems.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === '/'}
              className={({ isActive }) =>
                `flex items-center gap-3 rounded-lg px-4 py-3 text-sm font-medium transition-colors ${
                  isActive
                    ? 'bg-jade-500/10 text-jade-400 border-l-2 border-jade-500'
                    : 'text-gray-400 hover:bg-white/5 hover:text-white'
                }`
              }
            >
              <item.icon size={20} />
              <span>{item.label}</span>
            </NavLink>
          ))}
          <a
            href={canvasLink.href}
            className="flex items-center gap-3 rounded-lg px-4 py-3 text-sm font-medium text-gray-400 hover:bg-white/5 hover:text-white transition-colors"
          >
            <canvasLink.icon size={20} />
            <span>{canvasLink.label}</span>
          </a>
        </nav>

        {/* 系统状态：真实字段，不是数据源在线表 */}
        <div className="px-6 py-4 border-t border-white/5">
          <div className="flex items-center gap-2 mb-3 text-xs text-gray-500">
            <Database size={14} />
            <span>系统状态</span>
          </div>
          {!sys ? (
            <p className="text-xs text-gray-600">
              {sysError ? '未接入（状态接口不可用）' : '读取中…'}
            </p>
          ) : (
            <div className="space-y-1.5 text-xs text-gray-400">
              <div className="flex items-center gap-1.5">
                <span className={`h-2 w-2 rounded-full ${sys.llm ? 'bg-jade-500' : 'bg-red-500'}`} />
                <span className="truncate">LLM {sys.llm ? sys.llm_model : '不可用'}</span>
              </div>
              <div className="flex items-center gap-1.5">
                <span className="h-2 w-2 rounded-full bg-jade-500" />
                <span>存证 {sys.provenance_total} 条 / {sys.provenance_sources} 源</span>
              </div>
              <div className="flex items-center gap-1.5">
                <span className={`h-2 w-2 rounded-full ${sys.respect_robots ? 'bg-jade-500' : 'bg-gold-500'}`} />
                <span>robots {sys.respect_robots ? '遵守' : '未启用'}</span>
              </div>
            </div>
          )}
        </div>
      </aside>

      {/* Main Content */}
      <div className="flex flex-1 flex-col overflow-hidden">
        {/* Top Bar */}
        <header className="flex items-center justify-between border-b border-white/5 bg-ocean-950/50 px-6 py-3">
          <button
            className="rounded-lg p-2 text-gray-400 hover:bg-white/5 lg:hidden"
            onClick={() => setSidebarOpen(!sidebarOpen)}
          >
            {sidebarOpen ? <X size={20} /> : <Menu size={20} />}
          </button>
          <div className="hidden lg:block text-sm text-gray-500">
            TradePilot · 跨境电商智能助手
          </div>
          <div className="flex items-center gap-3">
            {isAuthenticated && user ? (
              <>
                <div className="flex items-center gap-2 rounded-lg bg-jade-500/10 px-3 py-1.5">
                  <User size={14} className="text-jade-400" />
                  <span className="text-xs text-jade-400">{user.email}</span>
                </div>
                <button
                  onClick={() => { logoutUser(); logout(); navigate('/'); }}
                  className="flex items-center gap-1 rounded-lg bg-white/5 px-3 py-1.5 text-xs text-gray-400 hover:bg-white/10 hover:text-white transition"
                >
                  <LogOut size={14} />
                  退出
                </button>
              </>
            ) : (
              <button onClick={() => navigate('/login')} className="rounded-lg bg-jade-500/10 px-3 py-1.5 text-xs text-jade-400 hover:bg-jade-500/20">
                登录
              </button>
            )}
            <span className="text-xs text-gray-500">
              {new Date().toLocaleDateString('zh-CN')}
            </span>
          </div>
        </header>

        {/* Page Content */}
        <main className="flex-1 overflow-y-auto p-6">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
