/**
 * TradePilot API 客户端（M2：从海丝前端迁入并重写数据来源）
 *
 * 原则：
 * - 只对接 TradePilot 真实后端（/api/agents/*），不再有任何 mock/模拟数据；
 * - 尚未迁移的模块（选品/内容/合规/全链路）明确抛「整合中」错误，
 *   由页面错误态展示——不允许用假数据糊住界面；
 * - 后端返回的 data_quality（real/sample/sample_faq）原样透传，页面必须展示。
 */
const BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api';

// ===== 数据类型定义（保持与页面契约一致）=====
export interface CategoryInfo {
  key: string;
  name: string;
  description: string;
}

export interface HotCategory { name: string; share: string; growth: string }
export interface Trend { description: string; impact: 'high' | 'medium' | 'low' }
export interface PriceTier { tier: string; price_range: string; volume_share: string }
export interface Competitor { name: string; origin: string; market_share: string; strength: string }
export interface Recommendation { product: string; predicted_sales: string; rating: string; reason: string }
export interface Risk { description: string; level: 'high' | 'medium' | 'low'; mitigation: string }

export interface MarketInsightData {
  category: string;
  region: string;
  market_size: string;
  market_growth: string;
  hot_categories: HotCategory[];
  trends: Trend[];
  price_tiers: PriceTier[];
  competitors: Competitor[];
  recommendations: Recommendation[];
  risks: Risk[];
  data_sources: string[];
  /** 后端数据质量标记：real=真实抓取带存证；sample=样本兜底（必须明示） */
  data_quality?: 'real' | 'sample';
  note?: string;
  provenance_ids?: number[];
  generated_by?: string;
}

export interface ProductRecommendation {
  product: string; predicted_sales: string; rating: string; reason: string;
  scores: Record<string, number>; budget_fit: string; suggested_moq: number; estimated_roi: string;
}

export interface SupplyRecommendation {
  supplier: string; location: string; moq: number; price_range: string;
  rating: number; trade_assurance: boolean; recommended: boolean;
}

export interface SmartSelectionData {
  category: string; target_market: string; budget_level: string;
  overall_score: {
    total: number; details: Record<string, number>; level: string;
    /** 评分口径说明：分数衡量的是样本分析价值，不是需求量 */
    basis?: string; top_keyword?: string;
  };
  market_opportunity: {
    market_size: string; growth_rate: string;
    competition_level: string; entry_difficulty: string;
    batch_size?: number; currency?: string;
    /** 实际数据源（可能 ≠ 目标市场，必须展示） */
    data_source?: string; source_note?: string;
  };
  product_recommendations: ProductRecommendation[];
  supply_recommendations: SupplyRecommendation[];
  /** 供应链为何为空——后端必填，前端必须展示，不允许静默留白 */
  supply_note?: string;
  profit_analysis: {
    available?: boolean; reason?: string | null;
    cost_breakdown: Record<string, string>;
    revenue: Record<string, string>;
    break_even: Record<string, string>;
    positioning?: string | null;
    cost_basis_note?: string | null;
    assumptions?: string | null;
  };
  action_plan: Record<string, { name: string; tasks: string[] }>;
  data_quality?: 'real' | 'sample';
  /** 后端列出的数据缺口，前端在底部集中展示 */
  gaps?: string[];
  budget_note?: string;
  provenance_ids?: number[];
  keyword_origin?: string;
  scans_failed?: { keyword: string; error: string }[];
  scans_thin?: { keyword: string; reason: string }[];
  note?: string;
}


export interface ContentGenerationData {
  product_name: string; category: string; platform: string; target_language: string;
  platform_label?: string; language_label?: string;
  content: { title: string; description: string; highlights: { text: string; icon: string }[]; seo_keywords: string[] };
  marketing: {
    social_copy: { hook: string; pain_point: string; solution: string; cta: string; hashtags: string[] };
    ad_copy: { headline: string; body: string; cta_button: string };
  };
  platform_compliance: {
    title_length_ok: boolean; desc_length_ok: boolean; warnings: string[];
    title_len?: number; title_max?: number; desc_len?: number; desc_max?: number;
    language_ok?: boolean;
    /** 上限来自配置常量而非实时校验，前端必须标明 */
    rules_source?: string; rules_verified?: boolean;
  };
  /** 文案是生成内容，不是可溯源数据 */
  data_quality?: 'generated';
  generated_by?: string;
  llm_attempted?: boolean;
  /** LLM 调了但没用上的原因（截断/语言不合格），非空时界面必须展示 */
  llm_failure_reason?: string | null;
  latency_note?: string;
  market_facts?: {
    source: string; price_band: Record<string, number>; currency: string;
    competitor_n: number; provenance_ids: number[];
  } | null;
  provenance_ids?: number[];
  gaps?: string[];
  note?: string;
}

export interface ChatReply {
  session_id: string;
  message_count: number;
  reply: { text: string; type: string; priority: string; source: string; language: string };
  emotion: { type: string; label: string; color: string; confidence: number; matched_keywords: string[] } | null;
  dispute: { detected: boolean; type: string | null; label: string | null; auto_resolve: boolean; escalation_threshold: number | null } | null;
  needs_human_escalation: boolean;
  session_summary: { total_messages: number; dispute_count: number; dominant_emotion: string };
  data_quality?: string;
  note?: string;
}

export interface FAQData { faqs: { q_zh: string; a_zh: string; q_en: string; a_en: string }[] }

export interface PipelineResult {
  success: boolean; pipeline: string;
  state: {
    category: string; product_name: string;
    market_insight: MarketInsightData | null; smart_selection: SmartSelectionData | null;
    content_generation: ContentGenerationData | null; compliance: ComplianceResult | null;
    customer_service: FAQData | null; steps_completed: string[]; errors: string[];
  };
  summary: { category: string; product: string; steps_completed: number; total_steps: number; errors: number; duration_seconds: number };
}

// ===== 请求工具 =====
interface ApiEnvelope<T> {
  success?: boolean; agent?: string; data?: T; message?: string;
  data_quality?: string; executed_at?: string; detail?: Record<string, unknown>;
}

async function request<T>(url: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE_URL}${url}`, {
    headers: { 'Content-Type': 'application/json', ...authHeader(url) },
    ...options,
  });
  // 访问令牌过期（30 分钟）时静默续期一次再重放。/auth/ 自己不参与，
  // 否则续期失败会递归。
  if (res.status === 401 && !url.startsWith('/auth/') && getAccessToken()) {
    if (await tryRefresh()) {
      return request<T>(url, options);
    }
  }
  if (!res.ok) {
    throw new Error(`请求失败: ${res.status} ${res.statusText}`);
  }
  const json = (await res.json()) as ApiEnvelope<T>;
  // 后端失败信封：success=false 时 message 是可读原因（严格模式拒绝样本等）
  if (json.success === false) {
    throw new Error(json.message || '请求失败');
  }
  if (json.data !== undefined) {
    // 把信封上的 data_quality 挂到 data 上，页面可直接展示
    if (json.data && typeof json.data === 'object' && json.data_quality) {
      (json.data as Record<string, unknown>).data_quality = json.data_quality;
    }
    return json.data as T;
  }
  return json as unknown as T;
}

// ===== 认证（M3：argon2id + JWT + 刷新令牌轮换）=====
// 密钥只存内存态变量与 localStorage（SPA 无法安全存 refresh token，
// 这是浏览器端的现实约束；真正的高安全要求走 BFF + HttpOnly Cookie，M4 评估）。
const ACCESS_KEY = 'tp_access_token';
const REFRESH_KEY = 'tp_refresh_token';

export interface AuthUser {
  user_id: string;
  email: string;
  tenant_id: string;
  company?: string;
  role?: string;
}

export interface AuthSession {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
  user: AuthUser;
}

export function getAccessToken(): string {
  return localStorage.getItem(ACCESS_KEY) || '';
}

export function setSession(s: AuthSession): void {
  localStorage.setItem(ACCESS_KEY, s.access_token);
  localStorage.setItem(REFRESH_KEY, s.refresh_token);
}

export function clearSession(): void {
  localStorage.removeItem(ACCESS_KEY);
  localStorage.removeItem(REFRESH_KEY);
}

export function hasSession(): boolean {
  return Boolean(getAccessToken());
}

function authHeader(url: string): Record<string, string> {
  // 有令牌就带上。曾经对 /auth/ 一律不带——但 /auth/me 恰恰需要令牌，
  // 于是刷新页面后永远 401。登录/注册带了过期令牌也无害，不值得为它开特例。
  const token = getAccessToken();
  if (!token) return {};
  return { Authorization: `Bearer ${token}` };
}

/** 静默续期：用 refresh 换新的一对。失败就清空会话（旧 refresh 已无效）。 */
async function tryRefresh(): Promise<boolean> {
  const rt = localStorage.getItem(REFRESH_KEY);
  if (!rt) return false;
  try {
    const res = await fetch(`${BASE_URL}/auth/refresh`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: rt }),
    });
    if (!res.ok) {
      clearSession();
      return false;
    }
    const data = (await res.json()) as AuthSession & { user?: AuthUser };
    localStorage.setItem(ACCESS_KEY, data.access_token);
    localStorage.setItem(REFRESH_KEY, data.refresh_token);
    return true;
  } catch {
    return false;
  }
}

/** 后端错误统一是 {detail: "..."}（HTTPException），取人话出来。 */
async function readError(res: Response, fallback: string): Promise<string> {
  try {
    const data = (await res.json()) as { detail?: string; message?: string };
    return data.detail || data.message || fallback;
  } catch {
    return fallback;
  }
}

export async function registerUser(
  email: string, password: string, company: string,
): Promise<AuthSession> {
  const res = await fetch(`${BASE_URL}/auth/register`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password, company }),
  });
  if (!res.ok) throw new Error(await readError(res, '注册失败'));
  const data = (await res.json()) as AuthSession & { success: boolean };
  setSession(data);
  return data;
}

export async function loginUser(email: string, password: string): Promise<AuthSession> {
  const res = await fetch(`${BASE_URL}/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password }),
  });
  if (!res.ok) throw new Error(await readError(res, '邮箱或密码错误'));
  const data = (await res.json()) as AuthSession;
  setSession(data);
  return data;
}

export async function logoutUser(): Promise<void> {
  const rt = localStorage.getItem(REFRESH_KEY);
  if (rt) {
    await fetch(`${BASE_URL}/auth/logout`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: rt }),
    }).catch(() => undefined);
  }
  clearSession();
}

export async function fetchMe(): Promise<AuthUser> {
  return request<AuthUser>('/auth/me');
}

async function postRequest<T>(url: string, body: unknown): Promise<T> {
  return request<T>(url, { method: 'POST', body: JSON.stringify(body) });
}

// ===== 品类与市场（能力声明，非数据）=====
const CATEGORIES: CategoryInfo[] = [
  { key: '宠物用品', name: '宠物用品', description: '狗床/猫砂盆/自动喂食器等高增长品类' },
  { key: '家居用品', name: '家居用品', description: '收纳/厨房/智能家居' },
  { key: '3C数码', name: '3C数码', description: '充电配件/音频/手机配件' },
  { key: '服装', name: '服装', description: 'T恤/家居服等基础款' },
];

/** 市场代码映射：前端用中文市场名，后端用市场代码。
 *
 * 只有 US 有真实可抓源；SEA 官方 API 待 M3。其余市场后端会如实返回
 * "没有可抓取的数据源"，前端把该消息原样展示——不在这里偷偷兜到 US，
 * 那会让用户以为在看中东数据、实际看的是美国数据。
 */
export function toMarketplace(region?: string): string {
  if (!region) return 'US';
  if (/美国|北美|欧美|US/i.test(region)) return 'US';
  if (/东南亚|SEA/i.test(region)) return 'SEA';
  return region;
}

export async function fetchCategories(): Promise<CategoryInfo[]> {
  return CATEGORIES;
}

export interface Capabilities {
  categories: number;
  categories_with_builtin_keywords: string[];
  languages: number;
  languages_with_template: number;
  platforms: number;
  compliance_countries: number;
  compliance_official_source_countries: number;
  agents: number;
  note: string;
}

/** 能力声明（能做什么），不是市场规模等业务数据。首页的数字全部来自这里。 */
export async function fetchCapabilities(): Promise<Capabilities> {
  return request<Capabilities>('/capabilities');
}

// ===== 市场洞察（M1 已接入真实抓取 + 存证）=====
export async function fetchMarketInsight(
  category: string,
  region?: string,
): Promise<MarketInsightData> {
  const raw = await postRequest<Record<string, any>>('/agents/market-insight', {
    category,
    marketplace: toMarketplace(region),
    allow_sample: true,
  });

  const tiers: PriceTier[] = (raw.price_tiers || []).map((t: Record<string, unknown>) => ({
    tier: String(t.tier || ''),
    price_range: String(t.price_range || ''),
    volume_share: String(t.volume_share || ''),
  }));

  const competitors: Competitor[] = (raw.competitors || []).map((c: Record<string, any>) => ({
    name: String(c.title || '').slice(0, 48),
    origin: String(c.source || ''),
    market_share: `${c.price} ${c.currency || ''}`,
    strength: c.provenance_id ? `存证#${c.provenance_id}` : '',
  }));

  return {
    category: String(raw.category || category),
    region: String(raw.marketplace || toMarketplace(region)),
    market_size: String(raw.summary?.summary || raw.market_size || ''),
    market_growth: String(raw.market_growth || ''),
    hot_categories: (raw.hot_categories || []) as HotCategory[],
    trends: (raw.trends || []) as Trend[],
    price_tiers: tiers,
    competitors,
    recommendations: (raw.recommendations || []) as Recommendation[],
    risks: (raw.risks || []) as Risk[],
    data_sources: (raw.sources_tried || []) as string[],
    data_quality: raw.data_quality,
    note: raw.note,
    provenance_ids: raw.provenance_ids,
    generated_by: raw.generated_by,
  };
}

// ===== 智能选品（M2 已接入：真实扫描 + 定价沙盘，无样本兜底）=====
export async function fetchSmartSelection(
  category: string,
  budget?: string,
  targetMarket?: string,
  keyword?: string,
): Promise<SmartSelectionData> {
  const raw = await postRequest<Record<string, any>>('/agents/smart-selection', {
    category,
    marketplace: toMarketplace(targetMarket),
    budget: budget || '中',
    // 用户给的检索词优先（后端标记 keyword_origin=user）；
    // 不给则靠品类取内置假设词，无内置词的品类后端会明确要求提供
    keyword: (keyword || '').trim(),
  });

  const products: ProductRecommendation[] = (raw.product_recommendations || []).map(
    (p: Record<string, any>) => ({
      product: String(p.product || ''),
      predicted_sales: String(p.predicted_sales || ''),
      rating: String(p.rating || ''),
      reason: String(p.reason || ''),
      scores: (p.scores || {}) as Record<string, number>,
      budget_fit: String(p.budget_fit || ''),
      suggested_moq: String(p.suggested_moq || ''),
      estimated_roi: String(p.estimated_roi || ''),
    }),
  );

  return {
    category: String(raw.category || category),
    target_market: String(raw.target_market || toMarketplace(targetMarket)),
    budget_level: String(raw.budget_level || budget || '中'),
    overall_score: {
      total: Number(raw.overall_score?.total || 0),
      details: (raw.overall_score?.details || {}) as Record<string, number>,
      level: String(raw.overall_score?.level || ''),
      basis: raw.overall_score?.basis,
      top_keyword: raw.overall_score?.top_keyword,
    },
    market_opportunity: {
      market_size: String(raw.market_opportunity?.market_size || ''),
      growth_rate: String(raw.market_opportunity?.growth_rate || ''),
      competition_level: String(raw.market_opportunity?.competition_level || ''),
      entry_difficulty: String(raw.market_opportunity?.entry_difficulty || ''),
      batch_size: raw.market_opportunity?.batch_size,
      currency: raw.market_opportunity?.currency,
      data_source: raw.market_opportunity?.data_source,
      source_note: raw.market_opportunity?.source_note,
    },
    product_recommendations: products,
    // 供应链：后端当前恒为空列表（未接入数据源），这里不做任何本地补位
    supply_recommendations: (raw.supply_recommendations || []) as SupplyRecommendation[],
    supply_note: raw.supply_note,
    profit_analysis: {
      available: raw.profit_analysis?.available,
      reason: raw.profit_analysis?.reason,
      cost_breakdown: (raw.profit_analysis?.cost_breakdown || {}) as Record<string, string>,
      revenue: (raw.profit_analysis?.revenue || {}) as Record<string, string>,
      break_even: (raw.profit_analysis?.break_even || {}) as Record<string, string>,
      positioning: raw.profit_analysis?.positioning,
      cost_basis_note: raw.profit_analysis?.cost_basis_note,
      assumptions: raw.profit_analysis?.assumptions,
    },
    action_plan: (raw.action_plan || {}) as Record<string, { name: string; tasks: string[] }>,
    data_quality: raw.data_quality,
    gaps: raw.gaps,
    budget_note: raw.budget_note,
    provenance_ids: raw.provenance_ids,
    keyword_origin: raw.keyword_origin,
    scans_failed: raw.scans_failed,
    scans_thin: raw.scans_thin,
    note: raw.note,
  };
}

// ===== 数据源状态：返回空列表（诚实：无真实源状态可报）=====
/** 系统状态：LLM 是否可用、存证条数、来源数、robots 遵守。
 *  之前这里是 `return []`，侧边栏只能显示"暂无数据源"；
 *  更早的版本干脆填一批假的 online 状态——比不显示更糟。 */
export interface SystemStatus {
  status: string;
  llm: boolean;
  llm_model: string;
  provenance_total: number;
  provenance_sources: number;
  respect_robots: boolean;
}

export async function fetchSystemStatus(): Promise<SystemStatus> {
  const raw = await request<Record<string, any>>('/health');
  return {
    status: String(raw.status || 'unknown'),
    llm: Boolean(raw.llm),
    llm_model: String(raw.llm_model || ''),
    provenance_total: Number(raw.provenance?.total ?? 0),
    provenance_sources: Number(raw.provenance?.distinct_sources ?? 0),
    respect_robots: Boolean(raw.respect_robots),
  };
}

// ===== 跨境内容生成（M2 已接入：多语言 × 多平台，产出为生成内容）=====
export async function generateContent(params: {
  product_name: string; category: string; platform?: string; target_language?: string;
  marketplace?: string; keyword?: string;
}): Promise<ContentGenerationData> {
  const raw = await postRequest<Record<string, any>>('/agents/content-generation', {
    product_name: params.product_name,
    category: params.category,
    platform: params.platform || 'shopee',
    target_language: params.target_language || 'en',
    marketplace: params.marketplace || 'US',
    keyword: params.keyword || '',
    use_market_data: true,
  });

  const hl = (raw.content?.highlights || []).map((h: unknown) =>
    typeof h === 'string' ? { icon: '✓', text: h }
      : { icon: String((h as Record<string, unknown>).icon || '✓'),
          text: String((h as Record<string, unknown>).text || '') });

  return {
    product_name: String(raw.product_name || params.product_name),
    category: String(raw.category || params.category),
    platform: String(raw.platform || params.platform || ''),
    target_language: String(raw.target_language || params.target_language || ''),
    platform_label: raw.platform_label,
    language_label: raw.language_label,
    content: {
      title: String(raw.content?.title || ''),
      description: String(raw.content?.description || ''),
      highlights: hl,
      seo_keywords: (raw.content?.seo_keywords || []) as string[],
    },
    marketing: {
      social_copy: {
        hook: String(raw.marketing?.social_copy?.hook || ''),
        pain_point: String(raw.marketing?.social_copy?.pain_point || ''),
        solution: String(raw.marketing?.social_copy?.solution || ''),
        cta: String(raw.marketing?.social_copy?.cta || ''),
        hashtags: (raw.marketing?.social_copy?.hashtags || []) as string[],
      },
      ad_copy: {
        headline: String(raw.marketing?.ad_copy?.headline || ''),
        body: String(raw.marketing?.ad_copy?.body || ''),
        cta_button: String(raw.marketing?.ad_copy?.cta_button || ''),
      },
    },
    platform_compliance: {
      title_length_ok: Boolean(raw.platform_compliance?.title_length_ok),
      desc_length_ok: Boolean(raw.platform_compliance?.desc_length_ok),
      warnings: (raw.platform_compliance?.warnings || []) as string[],
      title_len: raw.platform_compliance?.title_len,
      title_max: raw.platform_compliance?.title_max,
      desc_len: raw.platform_compliance?.desc_len,
      desc_max: raw.platform_compliance?.desc_max,
      language_ok: raw.platform_compliance?.language_ok,
      rules_source: raw.platform_compliance?.rules_source,
      rules_verified: raw.platform_compliance?.rules_verified,
    },
    data_quality: raw.data_quality,
    generated_by: raw.generated_by,
    llm_attempted: raw.llm_attempted,
    llm_failure_reason: raw.llm_failure_reason,
    latency_note: raw.latency_note,
    market_facts: raw.market_facts,
    provenance_ids: raw.provenance_ids,
    gaps: raw.gaps,
    note: raw.note,
  };
}

/** 支持的语言与平台：由后端返回，避免前端抄一份后与后端漂移 */
export interface ContentOptions {
  languages: { code: string; label: string; english: string; template_fallback: boolean }[];
  platforms: { code: string; label: string; title_max: number; desc_max: number; highlight_n: number }[];
  rules_source: string;
  rules_verified: boolean;
  llm_available: boolean;
}

export async function fetchContentOptions(): Promise<ContentOptions> {
  return request<ContentOptions>('/agents/content-options');
}

export async function fetchSupportedLanguages(): Promise<string[]> {
  const o = await fetchContentOptions();
  return o.languages.map((l) => l.code);
}

export async function fetchSupportedPlatforms(): Promise<string[]> {
  const o = await fetchContentOptions();
  return o.platforms.map((p) => p.code);
}

// ===== 合规助手（M2 已接入：美国官方源实时验证 + 东南亚静态分级）=====
export type VerificationKind =
  | 'clause_matched' | 'page_only' | 'blocked' | 'offline' | 'static_reference';

export interface ComplianceCheckItem {
  item: string;
  authority: string;
  kind: string;
  level: string;
  level_key: 'certification' | 'labeling' | 'platform';
  verification: VerificationKind;
  verified: boolean;
  page_url?: string | null;
  source_url?: string | null;
  excerpt?: string | null;
  note?: string;
  provenance_id?: number | null;
}

export interface ComplianceResult {
  category: string;
  category_key: string;
  target_country: string;
  market: string;
  checks: ComplianceCheckItem[];
  verified_count: number;
  total: number;
  mandatory_count: number;
  risk_level: string;
  risk_basis: string;
  recommendations: string[];
  gaps: string[];
  basis: string;
  provenance_ids: number[];
  note: string;
  data_quality?: string;
}

export interface ComplianceOption {
  name: string;
  market: string;
  official_source: boolean;
  note: string;
}

export interface ComplianceOptions {
  countries: ComplianceOption[];
  categories: string[];
  tariff_api: boolean;
  tariff_note: string;
}

export async function fetchComplianceOptions(): Promise<ComplianceOptions> {
  return request<ComplianceOptions>('/agents/compliance-options');
}

export async function fetchComplianceCheck(
  category: string,
  targetCountry: string = '美国',
): Promise<ComplianceResult> {
  // 美国要现场抓官方页面逐条定位条款原文，几十秒属正常
  return postRequest<ComplianceResult>('/agents/compliance', {
    category,
    target_country: targetCountry,
  });
}

// 国家列表不再在前端硬编码：后端 /agents/compliance-options 是唯一事实源，
// 前端抄一份必然漂移，还会让用户以为东南亚也有官方源实时验证。

// ===== 智能客服（M1 已接入：情绪/纠纷/FAQ + LLM）=====
export async function sendChatMessage(params: {
  message: string; category?: string; language?: string; session_id?: string;
}): Promise<ChatReply> {
  const raw = await postRequest<Record<string, any>>('/agents/customer-chat', {
    message: params.message,
    category: params.category || '通用',
    language: params.language || 'zh',
    session_id: params.session_id || 'default',
  });

  const emotion = raw.emotion
    ? {
        type: String(raw.emotion.type || 'neutral'),
        label: String(raw.emotion.label || '中性'),
        color: raw.emotion.type === 'angry' ? '#ef4444'
          : raw.emotion.type === 'negative' ? '#f59e0b'
          : raw.emotion.type === 'positive' ? '#10b981' : '#6b7280',
        confidence: 0.8,
        matched_keywords: [] as string[],
      }
    : null;

  return {
    session_id: String(raw.session_id || 'default'),
    message_count: Number(raw.message_count || 0),
    reply: {
      text: String(raw.reply || ''),
      type: 'answer',
      priority: 'normal',
      source: raw.data_quality || 'sample_faq',
      language: String(raw.language || 'zh'),
    },
    emotion,
    dispute: raw.dispute
      ? {
          detected: true,
          type: String(raw.dispute.type || ''),
          label: String(raw.dispute.label || ''),
          auto_resolve: raw.dispute.level !== 'critical',
          escalation_threshold: raw.dispute.level === 'critical' ? 0 : null,
        }
      : { detected: false, type: null, label: null, auto_resolve: false, escalation_threshold: null },
    needs_human_escalation: raw.dispute?.level === 'critical',
    session_summary: {
      total_messages: Number(raw.message_count || 0),
      dispute_count: raw.dispute ? 1 : 0,
      dominant_emotion: String(raw.emotion?.label || '中性'),
    },
    data_quality: raw.data_quality,
    note: raw.note,
  };
}

export async function fetchFAQ(_category: string, _language: string = 'zh'): Promise<FAQData> {
  return { faqs: [] };  // 无 FAQ 查询端点；客服对话时后端会返回命中条目
}

// ===== 全链路（M2 待接入）=====
export async function runPipeline(params: {
  category: string; region?: string; budget?: string; target_country?: string;
  product_name?: string; platform?: string; target_language?: string;
}): Promise<PipelineResult> {
  // 同步版本供脚本调用；浏览器走 streamPipeline（整链 2–3 分钟，同步请求会像卡死）
  return postRequest<PipelineResult>('/agents/pipeline', {
    category: params.category,
    marketplace: toMarketplace(params.region),
    budget: params.budget || '中',
    target_country: params.target_country || '美国',
    product_name: params.product_name || '',
    platform: params.platform || 'shopee',
    target_language: params.target_language || 'en',
  });
}

export interface PipelineEvent {
  event: 'step' | 'done';
  step?: string;
  label?: string;
  status?: 'running' | 'done' | 'failed' | 'skipped';
  duration?: number;
  quality?: string;
  summary?: string;
  message?: string;
  data?: unknown;
  state?: Record<string, unknown>;
  note?: string;
}

/** SSE 版全链路：每完成一步就回调一次，页面据此点亮节点。
 *
 * 不用 EventSource：它只支持 GET，而这里要 POST 一整套参数。
 * 用 fetch + ReadableStream 手读 SSE 帧，逐帧解析。
 */
export async function streamPipeline(
  params: {
    category: string; region?: string; budget?: string; target_country?: string;
    product_name?: string; platform?: string; target_language?: string;
    include_content?: boolean;
  },
  onEvent: (ev: PipelineEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch(`${BASE_URL}/agents/pipeline/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      category: params.category,
      marketplace: toMarketplace(params.region),
      budget: params.budget || '中',
      target_country: params.target_country || '美国',
      product_name: params.product_name || '',
      platform: params.platform || 'shopee',
      target_language: params.target_language || 'en',
      include_content: params.include_content !== false,
    }),
    signal,
  });
  if (!res.ok || !res.body) {
    throw new Error(`请求失败: ${res.status} ${res.statusText}`);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    // SSE 以空行分帧；末尾可能是不完整的一帧，留在 buffer 里等下一块
    const frames = buffer.split('\n\n');
    buffer = frames.pop() ?? '';
    for (const frame of frames) {
      const line = frame.split('\n').find((l) => l.startsWith('data:'));
      if (!line) continue;
      const payload = line.slice(5).trim();
      if (!payload || payload === '[DONE]') continue;
      try {
        onEvent(JSON.parse(payload) as PipelineEvent);
      } catch {
        // 半帧/非 JSON 一律跳过，不因一帧坏了中断整条流
      }
    }
  }
}

export async function fetchPipelineInfo(): Promise<unknown> {
  return { pipeline: 'cross_border_full', steps: 5, status: 'M2 整合中' };
}

// ===== 运营后台（M4）：X-Admin-Token 认证，独立于租户 JWT =====
// 令牌存 sessionStorage（标签页关了就清），绝不进 localStorage——
// 运营令牌权限是平台级的，比用户会话贵重得多。

export interface AdminOverview {
  auth: { tenants: number; users: number; active_refresh_tokens: number };
  tenants: { tenants: { id: string; name: string; company: string; created_at: string }[] };
  quota: { day: string; active_tenants: number; total_weight: number; limit_per_tenant: number };
  provenance: { total: number; distinct_sources: number };
  missions_total: number;
  llm_online: boolean;
  llm_model: string;
  system: { uptime_s: number; rss_mb: number | null; requests_total: number;
            errors_5xx: number; alarms: string[] };
}

export async function fetchAdminOverview(token: string): Promise<AdminOverview> {
  const res = await fetch(`${BASE_URL}/admin/overview`, {
    headers: { 'X-Admin-Token': token },
  });
  if (res.status === 403) throw new Error('运营后台未在服务端启用（未配置 TRADEPILOT_ADMIN_TOKEN）');
  if (res.status === 401) throw new Error('运营令牌无效');
  if (!res.ok) throw new Error(`加载失败（HTTP ${res.status}）`);
  return res.json() as Promise<AdminOverview>;
}

export interface AdminTrends {
  days: number;
  missions_by_day: { day: string; missions: number }[];
  quota_by_day: { day: string; weight: number }[];
  provenance_by_kind: Record<string, number>;
}

export async function fetchAdminTrends(token: string, days = 14): Promise<AdminTrends> {
  const res = await fetch(`${BASE_URL}/admin/trends?days=${days}`, {
    headers: { 'X-Admin-Token': token },
  });
  if (res.status === 403) throw new Error('运营后台未在服务端启用');
  if (res.status === 401) throw new Error('运营令牌无效');
  if (!res.ok) throw new Error(`加载失败（HTTP ${res.status}）`);
  return res.json() as Promise<AdminTrends>;
}

// ===== 平台集成（M4）：店铺授权 + 店铺级调用网关 =====
// 后端错误统一是 {detail: ...}，但店铺级调用网关的 502 detail 是对象
// {error_code, error_msg}（平台错误原样透传）——两种形态都要读出人话。

export interface ShopAuthorization {
  platform: string;
  shop_id: string;
  authorized_at: string;
  /** 进程内缓存口径：重启后即 false（要重新授权），如实展示不粉饰 */
  token_alive: boolean;
}

export interface ShopCallResult {
  success: boolean;
  platform: string;
  shop_id: string;
  data: unknown;
}

async function readDetailError(res: Response, fallback: string): Promise<string> {
  try {
    const data = (await res.json()) as { detail?: unknown; message?: string };
    const d = data.detail;
    if (typeof d === 'string' && d) return d;
    if (d && typeof d === 'object') {
      const obj = d as Record<string, unknown>;
      const code = obj.error_code ? `[${obj.error_code}] ` : '';
      if (obj.error_msg) return `${code}${String(obj.error_msg)}`;
    }
    return data.message || fallback;
  } catch {
    return fallback;
  }
}

/** 店铺授权页链接。409 = 凭证未配置（该去平台后台建 App），原样转述。 */
export async function fetchIntegrationAuthUrl(platform: string): Promise<string> {
  const res = await fetch(`${BASE_URL}/integrations/${platform}/auth-url`, {
    headers: authHeader(''),
  });
  if (!res.ok) throw new Error(await readDetailError(res, '获取授权链接失败'));
  const data = (await res.json()) as { auth_url: string };
  return data.auth_url;
}

/** 本租户已授权店铺清单（跨租户的店铺在这里根本不出现）。 */
export async function fetchIntegrationShops(platform: string): Promise<ShopAuthorization[]> {
  const res = await fetch(`${BASE_URL}/integrations/${platform}/shops`, {
    headers: authHeader(''),
  });
  if (res.status === 401) throw new Error('请先登录后再查看店铺');
  if (!res.ok) throw new Error(await readDetailError(res, '加载店铺清单失败'));
  const data = (await res.json()) as { shops: ShopAuthorization[] };
  return data.shops;
}

/** 授权 code 换店铺令牌。code 一次性、约 5 分钟过期。 */
export async function exchangeIntegrationCode(
  platform: string,
  body: { code: string; main_merchant_id?: string; redirect?: string },
): Promise<{ shop_id: string; has_refresh: boolean }> {
  const res = await fetch(`${BASE_URL}/integrations/${platform}/exchange`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeader('') },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(await readDetailError(res, '换令牌失败'));
  const data = (await res.json()) as { shop_id?: string; has_refresh?: boolean };
  return { shop_id: String(data.shop_id || ''), has_refresh: Boolean(data.has_refresh) };
}

/** 店铺级业务调用（经网关：授权校验 + 白名单）。409 = 需重新授权。 */
export async function shopApiCall(
  platform: string,
  shopId: string,
  body: { path: string; params?: Record<string, string>; method?: string },
): Promise<ShopCallResult> {
  const res = await fetch(
    `${BASE_URL}/integrations/${platform}/shops/${encodeURIComponent(shopId)}/call`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeader('') },
      body: JSON.stringify(body),
    },
  );
  if (!res.ok) throw new Error(await readDetailError(res, '调用失败'));
  return res.json() as Promise<ShopCallResult>;
}

// ===== 我的用量（M4）：配额余额 + 本人 token 账目 =====

export interface QuotaStatus {
  limited: boolean;
  used?: number;
  limit?: number;
  remaining?: number | null;
  note?: string;
}

export interface LlmUsageStats {
  day: string;
  today: { calls: number; prompt_tokens: number; completion_tokens: number };
  /** 后端按有账目的天返回——没消耗的天不在此列，前端展示时补零（平线=真的没用） */
  by_day: { day: string; calls: number; prompt_tokens: number; completion_tokens: number }[];
}

export interface MeUsage {
  tenant_id: string;
  quota: QuotaStatus;
  llm_usage: LlmUsageStats;
}

/** 当前租户用量自查（匿名没有账可查——401 原样转述）。 */
export async function fetchMeUsage(): Promise<MeUsage> {
  const res = await fetch(`${BASE_URL}/me/usage`, { headers: authHeader('') });
  if (res.status === 401) throw new Error('请先登录后再查看用量');
  if (!res.ok) throw new Error(await readDetailError(res, '加载用量失败'));
  return res.json() as Promise<MeUsage>;
}

/** Shopify 自定义 App token 直连：验证成功即落归属（token 明文不回传）。 */
export async function connectShopify(body: {
  shop: string; token: string;
}): Promise<{ success: boolean; shop_id: string; shop_name: string; note?: string }> {
  const res = await fetch(`${BASE_URL}/integrations/shopify/connect`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeader('') },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(await readDetailError(res, '连接失败'));
  return res.json() as Promise<{ success: boolean; shop_id: string; shop_name: string; note?: string }>;
}
