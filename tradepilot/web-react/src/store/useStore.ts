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
