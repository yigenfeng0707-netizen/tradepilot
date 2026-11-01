# 海丝出海 AI Agent · 前端

跨境电商智能全链路平台的 Web 前端，基于 **React 18 + TypeScript + Vite 6** 构建。

> 后端文档见 [`../README.md`](../README.md)。

---

## 技术栈

| 类别 | 技术 |
|------|------|
| 框架 | React 18 + TypeScript 5 |
| 构建 | Vite 6 |
| 样式 | TailwindCSS 3 |
| 动效 | Framer Motion 11 |
| 图表 | Recharts 2 |
| 状态管理 | Zustand 5 |
| 路由 | React Router 7 |
| 工具 | lucide-react（图标）、clsx + tailwind-merge |

## 页面

9 个页面，覆盖 5 大 Agent 的可视化展示 + 全链路工作流编排：

| 页面 | 路由 | 说明 |
|------|------|------|
| 首页 | `/` | Agent 状态面板 + 10 大品类导航 |
| 市场洞察 | `/market-insight` | 市场报告 |
| 智能选品 | `/smart-selection` | 选品推荐 + 评分圆环 + 利润分析 |
| 跨境内容 | `/content-generation` | 多语言内容生成（8 语言 × 4 平台） |
| 合规助手 | `/compliance` | 合规检查 + RCEP 关税计算 |
| 智能客服 | `/customer-service` | 客服对话演示 |
| 全链路工作流 | `/pipeline` | 5 步状态推进动画 |
| 定价 | `/pricing` | 商业定价 |
| 登录 | `/login` | 注册 / 登录 |

## 目录结构

```
web/src/
├── pages/          # 9 个页面
├── components/     # Layout / CategoryCard / ScoreCircle / ErrorBoundary / Empty
├── store/          # Zustand 单 store（品类 / 预算 / 目标市场 / 登录态）
└── utils/
    └── api.ts      # 请求封装 + HMAC-SHA256 签名（Web Crypto API）
```

## 开发

```bash
npm install
npm run dev          # http://localhost:5173
npm run build        # 生产构建（tsc -b && vite build）
npm run lint         # ESLint
```

## 与后端联调

`vite.config.ts` 已配置开发代理：`/api` 请求自动转发到 `http://localhost:8000`。请先启动后端（见 [`../README.md`](../README.md)）。

生产环境通过环境变量指定后端地址：

```env
# web/.env
VITE_API_BASE_URL=https://your-api.vercel.app/api/v1
```

前端在 `utils/api.ts` 中用 Web Crypto API 生成 HMAC-SHA256 请求签名，与后端 `SignatureMiddleware` 对应——所有走 LLM 的请求需携带有效签名。

## 部署

Vercel 静态部署（`vercel.json` 配置 SPA rewrite 到 `index.html`），可与后端在同一 Vercel 项目一体化部署。

---

MIT License
