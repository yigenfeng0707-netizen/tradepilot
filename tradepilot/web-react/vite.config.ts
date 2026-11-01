import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tsconfigPaths from "vite-tsconfig-paths";

// TradePilot 前端（M2）：对接本地 FastAPI（默认 8080）。
// 生产构建产物由 FastAPI 静态托管或独立 CDN，不再依赖任何第三方开发徽标插件。
//
// base 必须是 '/app/'：FastAPI 把 dist 挂在 /app 下（app/main.py 的 react_app 路由）。
// 默认 base '/' 会让 index.html 引用 /assets/*.js，而该路径没有路由托管
// → 全部 404 → 整页白屏。实测踩过：截图是一片纯白，控制台才看得到 404。
export default defineConfig({
  base: '/app/',
  build: {
    sourcemap: 'hidden',
    chunkSizeWarningLimit: 1000,
    rollupOptions: {
      output: {
        manualChunks: {
          vendor: ['react', 'react-dom', 'react-router-dom'],
          charts: ['recharts'],
          motion: ['framer-motion'],
        },
      },
    },
  },
  server: {
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8080',
        changeOrigin: true,
      },
    },
  },
  plugins: [
    react(),
    tsconfigPaths()
  ],
})
