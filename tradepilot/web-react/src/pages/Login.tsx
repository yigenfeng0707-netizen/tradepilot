import { useState } from 'react';
import { motion } from 'framer-motion';
import { useNavigate } from 'react-router-dom';
import { Mail, Lock, Building, Eye, EyeOff, Ship, Globe, ShieldCheck } from 'lucide-react';
import { useStore } from '@/store/useStore';
import { loginUser, registerUser } from '@/utils/api';

type Tab = 'login' | 'register';

const inputCls =
  'w-full rounded-lg border border-white/10 bg-ocean-800 px-4 py-2.5 text-sm text-white placeholder-gray-500 outline-none transition focus:border-jade-500 focus:ring-1 focus:ring-jade-500/30';

function validateEmail(email: string): boolean {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email);
}

/** 密码下限 8 位——与后端 auth.password_problem 保持一致。
 *  之前前端写 6 位且把明文密码存进 localStorage 当"认证"，
 *  那不是登录是把密码抄在门框上。现在只有后端 argon2id 这一条路。 */
const MIN_PASSWORD = 8;

export default function Login() {
  const [tab, setTab] = useState<Tab>('login');
  const [showPwd, setShowPwd] = useState(false);
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [company, setCompany] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [registerSuccess, setRegisterSuccess] = useState(false);
  const navigate = useNavigate();
  const login = useStore((s) => s.login);

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setRegisterSuccess(false);

    if (!validateEmail(email)) {
      setError('请输入有效的邮箱地址');
      return;
    }
    if (password.length < MIN_PASSWORD) {
      setError(`密码长度不能少于${MIN_PASSWORD}位`);
      return;
    }

    setLoading(true);
    try {
      const session = await loginUser(email, password);
      login({
        id: session.user.user_id,
        email: session.user.email,
        company: session.user.company || '',
        plan: 'free',
      });
      navigate('/');
    } catch (err) {
      setError(err instanceof Error ? err.message : '登录失败，请稍后重试');
    } finally {
      setLoading(false);
    }
  };

  const handleRegister = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');

    if (!company.trim()) {
      setError('请输入公司名称');
      return;
    }
    if (!validateEmail(email)) {
      setError('请输入有效的邮箱地址');
      return;
    }
    if (password.length < MIN_PASSWORD) {
      setError(`密码长度不能少于${MIN_PASSWORD}位`);
      return;
    }
    if (password !== confirmPassword) {
      setError('两次输入的密码不一致');
      return;
    }

    setLoading(true);
    try {
      const session = await registerUser(email, password, company.trim());
      login({
        id: session.user.user_id,
        email: session.user.email,
        company: session.user.company || company.trim(),
        plan: 'free',
      });
      navigate('/');
    } catch (err) {
      setError(err instanceof Error ? err.message : '注册失败，请稍后重试');
      setLoading(false);
    }
  };

  const switchTab = (t: Tab) => {
    setTab(t);
    setError('');
    setPassword('');
    setConfirmPassword('');
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-ocean-900 p-4">
      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        className="glass-light w-full max-w-md rounded-2xl p-8"
      >
        <div className="mb-8 text-center">
          <div className="mb-3 inline-flex h-14 w-14 items-center justify-center rounded-2xl bg-jade-500/10">
            <Ship className="h-7 w-7 text-jade-400" />
          </div>
          <h1 className="font-display text-2xl text-white">TradePilot AI Agent</h1>
          <p className="mt-1 text-sm text-gray-400">跨境电商智能运营平台</p>
        </div>

        {registerSuccess && (
          <motion.div
            initial={{ opacity: 0, y: -8 }}
            animate={{ opacity: 1, y: 0 }}
            className="mb-4 rounded-lg bg-jade-500/10 p-3 text-center text-xs text-jade-400"
          >
            注册成功，已自动登录
          </motion.div>
        )}

        <div className="mb-6 grid grid-cols-2 rounded-lg bg-ocean-800 p-1">
          {(['login', 'register'] as Tab[]).map((t) => (
            <button
              key={t}
              onClick={() => switchTab(t)}
              className={`rounded-md py-2 text-sm font-medium transition ${
                tab === t ? 'bg-jade-600 text-white' : 'text-gray-400 hover:text-white'
              }`}
            >
              {t === 'login' ? '登录' : '注册'}
            </button>
          ))}
        </div>

        {error && (
          <div className="mb-4 rounded-lg bg-red-500/10 p-3 text-center text-xs text-red-400">
            {error}
          </div>
        )}

        <form onSubmit={tab === 'login' ? handleLogin : handleRegister} className="space-y-4">
          {tab === 'register' && (
            <div>
              <label className="mb-1.5 block text-xs text-gray-400">公司名称</label>
              <div className="relative">
                <Building size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-500" />
                <input
                  type="text"
                  value={company}
                  onChange={(e) => setCompany(e.target.value)}
                  placeholder="您的公司名称"
                  className={`${inputCls} pl-10`}
                />
              </div>
            </div>
          )}

          <div>
            <label className="mb-1.5 block text-xs text-gray-400">邮箱</label>
            <div className="relative">
              <Mail size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-500" />
              <input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="name@company.com"
                className={`${inputCls} pl-10`}
              />
            </div>
          </div>

          <div>
            <label className="mb-1.5 block text-xs text-gray-400">密码</label>
            <div className="relative">
              <Lock size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-500" />
              <input
                type={showPwd ? 'text' : 'password'}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder={tab === 'register' ? `至少 ${MIN_PASSWORD} 位` : '输入密码'}
                className={`${inputCls} pl-10 pr-10`}
              />
              <button
                type="button"
                onClick={() => setShowPwd(!showPwd)}
                className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-500 hover:text-gray-300"
              >
                {showPwd ? <EyeOff size={16} /> : <Eye size={16} />}
              </button>
            </div>
          </div>

          {tab === 'register' && (
            <div>
              <label className="mb-1.5 block text-xs text-gray-400">确认密码</label>
              <div className="relative">
                <Lock size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-500" />
                <input
                  type={showPwd ? 'text' : 'password'}
                  value={confirmPassword}
                  onChange={(e) => setConfirmPassword(e.target.value)}
                  placeholder="再次输入密码"
                  className={`${inputCls} pl-10`}
                />
              </div>
            </div>
          )}

          <button
            type="submit"
            disabled={loading}
            className="flex w-full items-center justify-center gap-2 rounded-lg bg-jade-600 py-2.5 text-sm font-medium text-white transition hover:bg-jade-500 disabled:opacity-50"
          >
            {loading ? (
              <>
                <Globe size={16} className="animate-spin" />
                {tab === 'login' ? '登录中...' : '注册中...'}
              </>
            ) : (
              tab === 'login' ? '登录' : '注册并登录'
            )}
          </button>
        </form>

        <p className="mt-6 flex items-center justify-center gap-1.5 text-center text-xs text-gray-500">
          <ShieldCheck size={12} />
          密码以 argon2id 散列存储，服务器不保存明文
        </p>
      </motion.div>
    </div>
  );
}
