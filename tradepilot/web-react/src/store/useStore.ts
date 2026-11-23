import { create } from 'zustand';

interface User {
  id: string;
  email: string;
  company: string;
  plan: 'free' | 'basic' | 'pro' | 'enterprise';
}

interface AppState {
  selectedCategory: string;
  budget: string;
  targetMarket: string;
  // 用户状态
  user: User | null;
  isAuthenticated: boolean;
  // 操作
  setSelectedCategory: (category: string) => void;
  setBudget: (budget: string) => void;
  setTargetMarket: (market: string) => void;
  login: (user: User) => void;
  logout: () => void;
}

// 品类名单的**单一前端来源**：与后端 /api/capabilities 的
// categories_with_builtin_keywords 名单核对一致（Home.tsx 的能力徽章按它匹配）。
// 增删品类必须两边核对：后端 sample_market.CATEGORY_KEYWORDS + 这里 +
// Home.tsx categoryData（后者只多带图标/描述，name 必须与本名单一致）。
export const categories = [
  '宠物用品',
  '家居用品',
  '美妆个护',
  '3C电子',
  '服装服饰',
  '母婴用品',
  '户外运动',
  '汽车配件',
  '食品零食',
  '办公文具',
];

export const useStore = create<AppState>((set) => ({
  selectedCategory: '宠物用品',
  budget: '中',
  // 默认「欧美」而非「东南亚」：当前只有 US 有真实可抓源，默认落在无源市场
  // 会让用户打开页面先看到一条失败提示。SEA 保留为可选项，选中后如实报错。
  targetMarket: '欧美',
  user: null,
  isAuthenticated: false,
  setSelectedCategory: (category) => set({ selectedCategory: category }),
  setBudget: (budget) => set({ budget: budget }),
  setTargetMarket: (market) => set({ targetMarket: market }),
  login: (user) => set({ user, isAuthenticated: true }),
  logout: () => set({ user: null, isAuthenticated: false }),
}));
