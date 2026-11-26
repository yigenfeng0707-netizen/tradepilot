// 重建 OPC 海南城市赛路演 Deck —— 图文并茂版（pptxgenjs）
// 设计：三明治结构（深色封面/结束 + 浅色内容页）；截图统一「窗口框」母题；
// 3 张原生图表（人工基线阶段 / 自主决策分布 / 人工 vs Agent 耗时）；
// 全部数字实时读自 assets/eval_results.json，截图读自 报名素材/shots/。
// 输出：D:/电商/OPC报名材料_四件套/路演Deck_TradePilot_OPC海南城市赛.pptx
const fs = require("fs");
const path = require("path");
const pptxgen = require("pptxgenjs");

const ROOT = "D:/电商/tradepilot";
const OUT_DIR = "D:/电商/OPC报名材料_四件套";
const SHOTS = path.join(ROOT, "报名素材", "shots");

// ---------- 数据（真实，来自 eval_results.json） ----------
const ev = JSON.parse(fs.readFileSync(path.join(ROOT, "assets/eval_results.json"), "utf8"));
const sum = ev.summary;
const catNames = { pet: "宠物用品", home: "家居用品", electronics: "3C 电子" };
const cats = Object.keys(sum);
const agentMin = {}; // 各品类单任务均值（分钟）
cats.forEach((c) => { agentMin[c] = +(sum[c].elapsed_mean_s / 60).toFixed(1); });
const overrideByCat = cats.map((c) => sum[c].llm_override_total);
const overrideTotal = overrideByCat.reduce((a, b) => a + b, 0);
const humanBaseline = ev.human_baseline;
const stages = humanBaseline.stages_min;
const labor = ev.labor_cost_equiv;
const trace = ev.source_traceability_overall;
const provTot = ev.provenance_totals;
const spot = ev.fact_spot_check_accuracy;

// ----------调色板（取自产品自身：深海蓝 + 玉绿 + 金） ----------
const BG_DARK = "0B2233";   // 深海蓝（封面/结束）
const BG_LIGHT = "F5F8FA";  // 浅蓝灰（内容页）
const PRIMARY = "0E7C6B";   // 玉绿
const ACCENT = "E8B94A";    // 金
const TEXT = "1B2E3C";
const MUTED = "5E7687";
const CARD_TINT = "EAF2F0";
const WHITE = "FFFFFF";

const FONT = "微软雅黑";
const W = 13.33, H = 7.5, M = 0.6;

// ---------- 截图（优先真实任务实录，缺则回放图） ----------
function shot(name) {
  const p = path.join(SHOTS, name);
  return fs.existsSync(p) ? p : null;
}
const SHOT_FINAL = shot("live-90-final.png") || shot("90-final.png");
const SHOT_DASHBOARD = shot("live-00-dashboard.png") || shot("00-dashboard.png");
const SHOT_DECISIONS = ["live-07-decision.png", "live-04-decision.png", "live-01-decision.png"]
  .map(shot).filter(Boolean);
const SHOT_PROV = shot("live-91-prov-modal.png") || shot("91-prov-modal.png");

// 截图统一「窗口框」：白底圆角 + 阴影 + 图内嵌
function screenshotFrame(slide, imgPath, x, y, w, h) {
  if (!imgPath) return false;
  slide.addShape("roundRect", {
    x: x - 0.06, y: y - 0.06, w: w + 0.12, h: h + 0.12,
    fill: { color: WHITE }, rectRadius: 0.08,
    shadow: { type: "outer", color: "0B2233", blur: 14, offset: 3, angle: 90, opacity: 0.18 },
  });
  slide.addImage({ path: imgPath, x, y, w, h, sizing: { type: "cover", w, h }, rounding: true });
  return true;
}

const SRC = "来源：tradepilot/assets/eval_results.json（9 轮实测 · 2026-10-31）";

// ---------- 构建 ----------
const pres = new pptxgen();
pres.layout = "LAYOUT_WIDE";
pres.author = "冯亦根";
pres.title = "跨境领航员 TradePilot · OPC 海南城市赛路演";
const FONT_OPTS = { fontFace: FONT };

const mkTitle = (slide, kicker, title) => {
  slide.addText(kicker, { x: M, y: 0.34, w: 8, h: 0.3, fontSize: 13, color: PRIMARY, bold: true, charSpacing: 3, margin: 0, ...FONT_OPTS });
  slide.addText(title, { x: M, y: 0.62, w: 11, h: 0.7, fontSize: 30, color: TEXT, bold: true, margin: 0, ...FONT_OPTS });
};

const sourceLine = (slide, txt = SRC) => {
  slide.addText(txt, { x: M, y: H - 0.42, w: 12.1, h: 0.3, fontSize: 11, color: MUTED, margin: 0, ...FONT_OPTS });
};

// ============ 1. 封面（深色） ============
{
  const s = pres.addSlide();
  s.background = { color: BG_DARK };
  if (SHOT_FINAL) {
    s.addImage({ path: SHOT_FINAL, x: 6.93, y: 0, w: 6.4, h: 7.5, sizing: { type: "cover", w: 6.4, h: 7.5 } });
    s.addShape("rect", { x: 6.93, y: 0, w: 6.4, h: 7.5, fill: { color: BG_DARK, transparency: 45 } });
  }
  s.addText("OPC 创新黑客松 2026 · 海南城市赛", { x: M, y: 1.0, w: 7, h: 0.4, fontSize: 15, color: ACCENT, bold: true, charSpacing: 4, margin: 0, ...FONT_OPTS });
  s.addText("跨境领航员 TradePilot", { x: M, y: 1.7, w: 8.5, h: 1.1, fontSize: 50, color: WHITE, bold: true, margin: 0, ...FONT_OPTS });
  s.addText("给一人公司用的跨境电商 AI 运营部", { x: M, y: 2.85, w: 8.5, h: 0.6, fontSize: 26, color: "BFE3DA", margin: 0, ...FONT_OPTS });
  s.addText("输入一句目标，AI 自主跑完选品到上架的 8 个环节", { x: M, y: 3.65, w: 8.5, h: 0.5, fontSize: 18, color: "D9E7EC", margin: 0, ...FONT_OPTS });
  s.addText([
    { text: "商业生产力赛道 · 独立开发者 冯亦根 · 2026-11", options: { breakLine: true } },
    { text: "在线 Demo（浏览器直接打开，匿名可用）", options: { breakLine: true } },
    { text: "https://gsym236998-tradepilot-opc.ms.show", options: { color: ACCENT } },
  ], { x: M, y: 5.9, w: 8.5, h: 1.2, fontSize: 14, color: "9FB8C7", paraSpaceAfter: 6, margin: 0, ...FONT_OPTS });
}

// ============ 2. 命题 ============
{
  const s = pres.addSlide();
  s.background = { color: BG_LIGHT };
  mkTitle(s, "开场 · OPENING", "命题：和 AI 一起，把想法变成生意");
  s.addText("一个人，想把中国的一个品类卖到美国。", { x: M, y: 1.75, w: 12, h: 0.6, fontSize: 26, color: TEXT, bold: true, margin: 0, ...FONT_OPTS });
  // 以前 vs 现在：两栏（无框，分组靠间距与色调）
  s.addText("以前 · 需要一个运营部", { x: M, y: 2.7, w: 5.6, h: 0.4, fontSize: 16, color: MUTED, bold: true, margin: 0, ...FONT_OPTS });
  const beforeRows = ["查竞品 —— 逐家翻价格", "算价格 —— 汇率到岸一头汗", "查法规 —— 认证条款逐条核", "写文案 —— 标题卖点五点描述"];
  beforeRows.forEach((t, i) => {
    s.addShape("rect", { x: M, y: 3.25 + i * 0.62, w: 0.1, h: 0.1, fill: { color: MUTED } });
    s.addText(t, { x: M + 0.28, y: 3.12 + i * 0.62, w: 5.4, h: 0.5, fontSize: 15, color: TEXT, margin: 0, ...FONT_OPTS });
  });
  s.addText("现在 · 一句话交给 TradePilot", { x: 7.1, y: 2.7, w: 5.6, h: 0.4, fontSize: 16, color: PRIMARY, bold: true, margin: 0, ...FONT_OPTS });
  const afterRows = [
    "自主跑完 8 个环节",
    "每个价格与每条法规都带回来源链接和抓取时间",
    "最后直接吐出一份能上架的包",
  ];
  afterRows.forEach((t, i) => {
    s.addShape("rect", { x: 7.1, y: 3.25 + i * 0.62, w: 0.1, h: 0.1, fill: { color: ACCENT } });
    s.addText(t, { x: 7.38, y: 3.12 + i * 0.62, w: 5.3, h: 0.5, fontSize: 15, color: TEXT, margin: 0, ...FONT_OPTS });
  });
  s.addText("我负责想法和决策，AI 负责把想法变成生意。", { x: M, y: 5.9, w: 12, h: 0.5, fontSize: 20, color: PRIMARY, bold: true, margin: 0, ...FONT_OPTS });
  sourceLine(s, "");
}

// ============ 3. 痛点 + 人工基线图 ============
{
  const s = pres.addSlide();
  s.background = { color: BG_LIGHT };
  mkTitle(s, "用户价值 25% · 痛点", "一个人，根本做不动");
  s.addText("2–4", { x: M, y: 1.7, w: 3, h: 1.3, fontSize: 72, color: ACCENT, bold: true, margin: 0, ...FONT_OPTS });
  s.addText("个人天", { x: M, y: 2.95, w: 3, h: 0.4, fontSize: 18, color: TEXT, bold: true, margin: 0, ...FONT_OPTS });
  s.addText("一个新品从选品到准备上架的前置调研工时", { x: M, y: 3.35, w: 3.4, h: 0.8, fontSize: 13, color: MUTED, margin: 0, ...FONT_OPTS });
  const pains = [
    ["链条长", "比价、汇率、到岸成本、认证、Listing——要多种角色，一个人请不起运营部"],
    ["分不清真假", "通用大模型能给文案，但给不出可回访的来源，使用者不知道哪句话可信"],
    ["慢就是死", "对一人公司不是「慢」，是新品根本推不动——时间花在调研而不是卖货上"],
  ];
  pains.forEach((p, i) => {
    const y = 1.75 + i * 1.25;
    s.addText(p[0], { x: 4.3, y, w: 2.6, h: 0.4, fontSize: 17, color: PRIMARY, bold: true, margin: 0, ...FONT_OPTS });
    s.addText(p[1], { x: 4.3, y: y + 0.42, w: 3.5, h: 0.9, fontSize: 13, color: TEXT, margin: 0, ...FONT_OPTS });
  });
  // 右侧：人工基线五阶段耗时（原生横向条形图，本人掐表真实数据）
  const stageNames = Object.keys(stages);
  const stageVals = stageNames.map((k) => +stages[k].toFixed(1));
  s.addText("人工基线 · 一次任务 65.4 分钟怎么花掉（本人掐表 2026-11-20）", { x: 8.3, y: 1.55, w: 4.6, h: 0.6, fontSize: 13, color: TEXT, bold: true, margin: 0, ...FONT_OPTS });
  s.addChart(pres.charts.BAR, [{
    name: "人工耗时（分钟）",
    labels: stageNames,
    values: stageVals,
  }], {
    x: 8.15, y: 2.1, w: 4.7, h: 3.5, barDir: "bar",
    chartColors: [PRIMARY], chartColorsLength: 1,
    showValue: true, dataLabelPosition: "outEnd", dataLabelColor: TEXT, dataLabelFontSize: 11,
    catAxisLabelColor: TEXT, catAxisLabelFontSize: 12, valAxisHidden: true,
    valGridLine: { style: "none" }, catGridLine: { style: "none" },
    showLegend: false, chartArea: { fill: { color: WHITE } },
    barGap: 40,
  });
  sourceLine(s);
}

// ============ 4. 方案：8 环节链路 ============
{
  const s = pres.addSlide();
  s.background = { color: BG_LIGHT };
  mkTitle(s, "场景落地 20% · 方案", "一句目标，自主编排 8 个环节");
  const steps = ["选品雷达", "竞品实采", "现场汇率", "定价沙盘", "合规体检", "关键词层", "Listing 工坊", "文案审计"];
  steps.forEach((t, i) => {
    const col = i % 4, row = Math.floor(i / 4);
    const x = M + col * 3.1, y = 1.8 + row * 1.15;
    s.addShape("roundRect", { x, y, w: 2.75, h: 0.92, fill: { color: i === steps.length - 1 ? ACCENT : CARD_TINT }, rectRadius: 0.08, line: { color: PRIMARY, width: i === steps.length - 1 ? 0 : 0.75, transparency: i === steps.length - 1 ? 100 : 60 } });
    s.addText(`${i + 1}`, { x: x + 0.14, y: y + 0.12, w: 0.5, h: 0.4, fontSize: 18, color: i === steps.length - 1 ? BG_DARK : PRIMARY, bold: true, margin: 0, ...FONT_OPTS });
    s.addText(t, { x: x + 0.62, y: y + 0.1, w: 2.0, h: 0.75, fontSize: 16, color: i === steps.length - 1 ? BG_DARK : TEXT, bold: true, valign: "middle", margin: 0, ...FONT_OPTS });
  });
  // 底部流程说明（row list）
  const rows = [
    "一句目标 → 逐步留存决策卡（候选集 / 得分 / 落选原因，可审计）",
    "→ 导出 DOCX 可上架包：拿到就能上架接单",
    "下一步不是写死的顺序：LLM 在可执行候选白名单内真实选择，由当前状态决定",
  ];
  rows.forEach((t, i) => {
    const y = 4.45 + i * 0.62;
    s.addShape("rect", { x: M, y: y + 0.2, w: 0.09, h: 0.09, fill: { color: ACCENT } });
    s.addText(t, { x: M + 0.26, y, w: 12.2, h: 0.5, fontSize: 15, color: i === 2 ? MUTED : TEXT, margin: 0, ...FONT_OPTS });
  });
  sourceLine(s);
}

// ============ 5. DEMO · 真实任务实录大图 ============
{
  const s = pres.addSlide();
  s.background = { color: BG_LIGHT };
  mkTitle(s, "场景落地 20% · DEMO", "现场演示：线上真实任务是什么样");
  screenshotFrame(s, SHOT_FINAL, 6.55, 1.55, 6.4, 4.0);
  s.addText("图中是今天线上真实任务跑完的运营大屏：左侧任务目标与决策时间线，右侧市场扫描 / 定价沙盘 / 合规体检 / Listing 与文案审计，全部是真实抓取数据。", { x: 6.55, y: 5.65, w: 6.4, h: 0.9, fontSize: 12, color: MUTED, margin: 0, ...FONT_OPTS });
  s.addText("三条可复现路径", { x: M, y: 1.7, w: 5.6, h: 0.4, fontSize: 17, color: PRIMARY, bold: true, margin: 0, ...FONT_OPTS });
  const paths = [
    ["① 打开 /app，启动默认目标", "「把宠物用品切入美国站」—— 决策时间线逐张出现 8 张决策卡，可点开候选集与存证弹层"],
    ["② 点 Listing 中任意「存证#N」", "或打开 /api/provenance——原文片段 + 单品页 URL + 时间戳 + 内容哈希，现场回访原页"],
    ["③ 任务完成后打开 /api/opc/brief", "并在 /app 导出上架包——输出「想法→生意」价值链简报；DOCX 上架包可直接接单"],
  ];
  paths.forEach((p, i) => {
    const y = 2.15 + i * 1.42;
    s.addText(p[0], { x: M, y, w: 5.7, h: 0.4, fontSize: 14, color: TEXT, bold: true, margin: 0, ...FONT_OPTS });
    s.addText(p[1], { x: M, y: y + 0.36, w: 5.7, h: 0.85, fontSize: 12, color: MUTED, margin: 0, ...FONT_OPTS });
  });
  s.addText("演示鲁棒性：每步有墙钟预算，慢源最多拖一步、不挂死整链；断网降级本地 live → 存档回放 → 离线视频", { x: M, y: 6.5, w: 5.7, h: 0.5, fontSize: 12, color: PRIMARY, margin: 0, ...FONT_OPTS });
  sourceLine(s, "在线 Demo：https://gsym236998-tradepilot-opc.ms.show（匿名可用 · 图为 2026-11-23 线上真实任务截图）");
}

// ============ 6. DEMO · 决策卡 ============
{
  const s = pres.addSlide();
  s.background = { color: BG_LIGHT };
  mkTitle(s, "场景落地 20% · 决策卡", "LLM 逐步决策，每一张都可审计");
  const imgs = SHOT_DECISIONS.slice(0, 3);
  imgs.forEach((img, i) => {
    screenshotFrame(s, img, M + i * 4.15, 1.6, 3.85, 2.4);
  });
  s.addText("决策卡随任务流式出现：候选集、每个候选的得分、为什么没选别人，任务跑完即归档可回放。", { x: M, y: 4.25, w: 12.2, h: 0.4, fontSize: 15, color: TEXT, margin: 0, ...FONT_OPTS });
  const feats = [
    ["不是固定序列", "LLM 在可执行候选白名单内真实选择，由当前状态决定下一步"],
    ["失败是状态驱动", "抓取受限自主换源重试，重试耗尽降级跳过、独立分支继续跑完"],
    ["回放即证据", "每张卡带候选集与落选原因，现场可点开逐项核对"],
  ];
  feats.forEach((f, i) => {
    const y = 4.85 + i * 0.68;
    s.addText(f[0], { x: M, y, w: 2.6, h: 0.4, fontSize: 14, color: PRIMARY, bold: true, margin: 0, ...FONT_OPTS });
    s.addText(f[1], { x: M + 2.7, y, w: 9.5, h: 0.4, fontSize: 13, color: TEXT, margin: 0, ...FONT_OPTS });
  });
  sourceLine(s, "图：2026-11-23 线上真实任务决策卡流式截图");
}

// ============ 7. 自主性（原生图表） ============
{
  const s = pres.addSlide();
  s.background = { color: BG_LIGHT };
  mkTitle(s, "技术实现 15% · 自主性", "LLM 真的在决策，不是盖章");
  s.addText(`${overrideTotal}`, { x: M, y: 1.65, w: 4, h: 1.4, fontSize: 84, color: ACCENT, bold: true, margin: 0, ...FONT_OPTS });
  s.addText("次 llm_override", { x: M, y: 3.0, w: 4, h: 0.4, fontSize: 18, color: TEXT, bold: true, margin: 0, ...FONT_OPTS });
  s.addText("9 轮评测里，模型选择与规则评分不一致并被采纳的次数——这是自主决策的证据", { x: M, y: 3.45, w: 4.2, h: 0.9, fontSize: 12, color: MUTED, margin: 0, ...FONT_OPTS });
  // 原生柱状图：各品类 override 次数
  const labels = cats.map((c) => catNames[c]);
  s.addText("各品类 3 轮实测中的 llm_override 次数", { x: 5.4, y: 1.65, w: 7.3, h: 0.4, fontSize: 13, color: TEXT, bold: true, margin: 0, ...FONT_OPTS });
  s.addChart(pres.charts.BAR, [{
    name: "llm_override 次数",
    labels,
    values: overrideByCat,
  }], {
    x: 5.2, y: 2.1, w: 7.5, h: 3.3, barDir: "col",
    chartColors: [PRIMARY], chartColorsLength: 1,
    showValue: true, dataLabelPosition: "outEnd", dataLabelColor: TEXT, dataLabelFontSize: 12,
    catAxisLabelColor: TEXT, catAxisLabelFontSize: 13, valAxisHidden: true,
    valGridLine: { style: "none" }, catGridLine: { style: "none" },
    showLegend: false, chartArea: { fill: { color: WHITE } },
    barGap: 60,
  });
  const rows = [
    ["两个数都披露", "llm_planned_rate（模型选择步数占比）与 llm_override_total 分开披露——不把前者当自主证据"],
    ["决策卡可审计", "每张卡记录候选集、得分、为什么没选别人，任务跑完即归档可回放"],
  ];
  rows.forEach((r, i) => {
    const y = 5.6 + i * 0.62;
    s.addText(r[0], { x: M, y, w: 2.3, h: 0.4, fontSize: 14, color: PRIMARY, bold: true, margin: 0, ...FONT_OPTS });
    s.addText(r[1], { x: M + 2.4, y: y + 0.02, w: 10.1, h: 0.4, fontSize: 13, color: TEXT, margin: 0, ...FONT_OPTS });
  });
  sourceLine(s);
}

// ============ 8. 信任底座（存证弹层） ============
{
  const s = pres.addSlide();
  s.background = { color: BG_LIGHT };
  mkTitle(s, "技术实现 15% · 信任底座", "宁可报错，也不编数据");
  screenshotFrame(s, SHOT_PROV, 6.7, 1.5, 6.1, 3.9);
  s.addText("「存证#N」弹层：原文片段 + 单品页 URL + 抓取时间戳 + 内容哈希，现场回访原页", { x: 6.7, y: 5.5, w: 6.1, h: 0.7, fontSize: 12, color: MUTED, margin: 0, ...FONT_OPTS });
  const stats = [
    [`${trace.facts_traceable}/${trace.facts_total}`, "界面展示事实点可溯源（分母有定义：不含派生值与 AI 文案）"],
    [`${provTot.total} / ${provTot.distinct_sources}`, "存证条数 / 独立来源，逐条可点开回访原文片段"],
    ["robots 闸门", "被禁止的路径如实拦截不绕过，编造的 id 一律被来源闸门拦下"],
  ];
  stats.forEach((st, i) => {
    const y = 1.65 + i * 1.35;
    s.addText(st[0], { x: M, y, w: 5.4, h: 0.6, fontSize: i === 2 ? 18 : 34, color: i === 2 ? PRIMARY : ACCENT, bold: true, margin: 0, ...FONT_OPTS });
    s.addText(st[1], { x: M, y: y + 0.6, w: 5.4, h: 0.7, fontSize: 13, color: TEXT, margin: 0, ...FONT_OPTS });
  });
  s.addText("拿不到真数的字段宁可不给：关键词搜索量不输出，合规条款三档核验（命中 / 仅域名可达 / 未命中），不假装通过。", { x: M, y: 5.9, w: 5.6, h: 0.8, fontSize: 13, color: MUTED, margin: 0, ...FONT_OPTS });
  sourceLine(s);
}

// ============ 9. 效率对比（原生图表） ============
{
  const s = pres.addSlide();
  s.background = { color: BG_LIGHT };
  mkTitle(s, "数据验证 · 诚实口径", `人工 ${humanBaseline.task_time_min} 分钟，Agent 几分钟`);
  // 人工 vs Agent（分钟，同口径单次任务）
  const labels = ["人工基线（本人掐表）", ...cats.map((c) => `Agent · ${catNames[c]}`)];
  const values = [+humanBaseline.task_time_min, ...cats.map((c) => agentMin[c])];
  s.addChart(pres.charts.BAR, [{
    name: "单次任务耗时（分钟）",
    labels,
    values,
  }], {
    x: M, y: 1.75, w: 7.4, h: 4.0, barDir: "col",
    chartColors: ["C0563F", PRIMARY, PRIMARY, PRIMARY], chartColorsLength: 4,
    showValue: true, dataLabelPosition: "outEnd", dataLabelColor: TEXT, dataLabelFontSize: 12,
    catAxisLabelColor: TEXT, catAxisLabelFontSize: 12, valAxisHidden: true,
    valGridLine: { style: "none" }, catGridLine: { style: "none" },
    showLegend: false, chartArea: { fill: { color: WHITE } },
    barGap: 60,
  });
  const stats = [
    [`${(labor.automation_rate * 100).toFixed(1)}%`, `自动化率（时薪 ¥${labor.hourly_cny} 为入参，人工等效成本 ¥${labor.labor_cost_equiv_cny}）`],
    [`${spot.checked}/${spot.total}`, `事实抽检：本人逐条回访核对，全部一致（${humanBaseline.performed_at.slice(0, 10)} 录入）`],
    ["0", "合成数据冒充实测——分母只算界面展示的事实点，派生值与 AI 文案不进"],
  ];
  stats.forEach((st, i) => {
    const y = 1.9 + i * 1.5;
    s.addText(st[0], { x: 8.5, y, w: 4.2, h: 0.55, fontSize: 30, color: ACCENT, bold: true, margin: 0, ...FONT_OPTS });
    s.addText(st[1], { x: 8.5, y: y + 0.55, w: 4.2, h: 0.8, fontSize: 12, color: TEXT, margin: 0, ...FONT_OPTS });
  });
  s.addText("红线：人工基线与抽检必须真人录入、agent 不自填——未录入时为 null，由本人掐表补齐。这是执行证据。", { x: 8.5, y: 6.35, w: 4.2, h: 0.6, fontSize: 11, color: MUTED, margin: 0, ...FONT_OPTS });
  sourceLine(s);
}

// ============ 10. 工程化 ============
{
  const s = pres.addSlide();
  s.background = { color: BG_LIGHT };
  mkTitle(s, "技术实现 15% · 工程", "工程化补人力：一人公司也交得出生产级");
  const rows = [
    ["621", "项离线单测全绿", "CI 含代码检查与覆盖率 85% 门禁，挡住退化"],
    ["12 分钟 → 60 秒", "弱网演示鲁棒性", "单步墙钟预算：慢源最多拖一步，不挂死整链"],
    ["漂移门禁", "发布面一致性", "CI 部署后自动校验工具 8/8 与前端字节级一致"],
    ["三档预案", "现场不掉链子", "线上 live → 本地 live → 存档回放 → 离线视频"],
  ];
  rows.forEach((r, i) => {
    const y = 1.8 + i * 1.18;
    s.addShape("roundRect", { x: M, y, w: 12.1, h: 1.0, fill: { color: i % 2 ? WHITE : CARD_TINT }, rectRadius: 0.06, shadow: i % 2 ? { type: "outer", color: "0B2233", blur: 8, offset: 2, angle: 90, opacity: 0.08 } : undefined });
    s.addText(r[0], { x: M + 0.25, y: y + 0.14, w: 2.6, h: 0.7, fontSize: 20, color: ACCENT, bold: true, valign: "middle", margin: 0, ...FONT_OPTS });
    s.addText(r[1], { x: M + 3.0, y: y + 0.1, w: 3.2, h: 0.4, fontSize: 15, color: PRIMARY, bold: true, margin: 0, ...FONT_OPTS });
    s.addText(r[2], { x: M + 3.0, y: y + 0.48, w: 8.8, h: 0.4, fontSize: 13, color: TEXT, margin: 0, ...FONT_OPTS });
  });
  sourceLine(s);
}

// ============ 11. 诚实披露 ============
{
  const s = pres.addSlide();
  s.background = { color: BG_LIGHT };
  mkTitle(s, "诚实披露 · 反幻觉红线", "不完美的地方，我讲在前面");
  const items = [
    ["electronics 有一轮选品源降级", "真实发生的，评测里如实记账 degraded。换源重试、耗尽降级跳过、独立分支继续跑完——降级但不中断，我把它当设计能力讲"],
    ["尚无付费用户与收入", "内测期如实免费，¥199/月已按市场对标拍板并同步前端标价；不拿「用户量」编故事"],
    ["关键词不给月搜索量", "拿不到真数就不给；只给真实标题里可复算的观测，附证据标题可回访"],
    ["当当是美国站竞品价吗？不是", "国内 CNY 参考价 ÷ 现场真实汇率折算，界面明确标 domestic_reference；真实 USD 竞品带走官方 API 授权，赛后接入"],
  ];
  items.forEach((it, i) => {
    const col = i % 2, row = Math.floor(i / 2);
    const x = M + col * 6.2, y = 1.7 + row * 2.2;
    s.addShape("roundRect", { x, y, w: 5.9, h: 1.95, fill: { color: WHITE }, rectRadius: 0.08, line: { color: "D8E2E8", width: 1 } });
    s.addText(it[0], { x: x + 0.25, y: y + 0.18, w: 5.4, h: 0.5, fontSize: 15, color: TEXT, bold: true, margin: 0, ...FONT_OPTS });
    s.addText(it[1], { x: x + 0.25, y: y + 0.72, w: 5.4, h: 1.1, fontSize: 12, color: MUTED, margin: 0, ...FONT_OPTS });
  });
  s.addText("赛事红线「合成数据一票否决」——这套系统的机制是：编造的数据在来源闸门处被拦下，或被如实标为 derived，混不进事实分母。", { x: M, y: 6.35, w: 12.1, h: 0.5, fontSize: 13, color: PRIMARY, bold: true, margin: 0, ...FONT_OPTS });
  sourceLine(s, "");
}

// ============ 12. 商业模式 ============
{
  const s = pres.addSlide();
  s.background = { color: BG_LIGHT };
  mkTitle(s, "商业模式 30% · 最高权重", "怎么赚钱：先验证价值主张，再谈规模");
  // 左：收入三条线
  s.addText("收入三条线", { x: M, y: 1.7, w: 6, h: 0.4, fontSize: 17, color: PRIMARY, bold: true, margin: 0, ...FONT_OPTS });
  const lines = [
    ["① SaaS 订阅（主营）", "¥199 / 月 · 内测期免费"],
    ["② 按品类的 Agent 定制", "按品类报价 · 私有部署"],
    ["③ 脱敏数据增值报告", "赛后规划"],
  ];
  lines.forEach((l, i) => {
    const y = 2.3 + i * 0.95;
    s.addText(l[0], { x: M, y, w: 6, h: 0.4, fontSize: 15, color: TEXT, bold: true, margin: 0, ...FONT_OPTS });
    s.addText(l[1], { x: M + 0.3, y: y + 0.4, w: 5.6, h: 0.4, fontSize: 12, color: MUTED, margin: 0, ...FONT_OPTS });
  });
  // 右：定价卡
  s.addShape("roundRect", { x: 7.4, y: 1.7, w: 5.3, h: 4.4, fill: { color: BG_DARK }, rectRadius: 0.1 });
  s.addText("定价", { x: 7.7, y: 1.95, w: 4.7, h: 0.4, fontSize: 15, color: ACCENT, bold: true, margin: 0, ...FONT_OPTS });
  s.addText("¥199", { x: 7.7, y: 2.4, w: 2.8, h: 1.0, fontSize: 54, color: WHITE, bold: true, margin: 0, ...FONT_OPTS });
  s.addText("/ 月 · 正式期", { x: 10.6, y: 3.05, w: 1.95, h: 0.4, fontSize: 13, color: "9FB8C7", margin: 0, ...FONT_OPTS });
  s.addText([
    { text: "内测期：免费（当前阶段）", options: { breakLine: true } },
    { text: "私有化部署：面议", options: { breakLine: true } },
    { text: "定价为 2026-11-20 按市场对标拍板，不是成本核算", options: {} },
  ], { x: 7.7, y: 3.6, w: 4.7, h: 1.8, fontSize: 13, color: "D9E7EC", paraSpaceAfter: 10, margin: 0, ...FONT_OPTS });
  // 底：成本与获客
  s.addText("成本（真实记账，非估算）", { x: M, y: 5.35, w: 6, h: 0.4, fontSize: 15, color: PRIMARY, bold: true, margin: 0, ...FONT_OPTS });
  s.addText("llm_usage 表按租户 × 天记 token：客服 86/650、Listing 239/1099（输入/输出）；网关单价待运营录入后回算 ¥199 毛利率——未实测的数字不写进商业说明", { x: M, y: 5.75, w: 6.2, h: 0.9, fontSize: 12, color: TEXT, margin: 0, ...FONT_OPTS });
  s.addText("获客：OPC 社群与跨境社群内容获客（本赛事社区即首个验证场）；城市赛 / 总决赛订单对接为优先渠道，获客成本暂无实测，如实披露", { x: 7.4, y: 6.35, w: 5.3, h: 0.8, fontSize: 11, color: MUTED, margin: 0, ...FONT_OPTS });
  sourceLine(s, "来源：tradepilot/docs/pricing-decision.md（2026-11-20 拍板并同步前端标价）");
}

// ============ 13. OPC 分工 ============
{
  const s = pres.addSlide();
  s.background = { color: BG_LIGHT };
  mkTitle(s, "OPC · 命题核心", "一个人 + AI 怎么做到");
  // 两栏：AI 执行 / 本人判断
  s.addShape("roundRect", { x: M, y: 1.7, w: 5.9, h: 4.3, fill: { color: CARD_TINT }, rectRadius: 0.08 });
  s.addText("AI 承担执行", { x: M + 0.3, y: 1.95, w: 5.3, h: 0.45, fontSize: 18, color: PRIMARY, bold: true, margin: 0, ...FONT_OPTS });
  s.addText([
    { text: "8 个环节的执行与逐步决策（候选白名单内真实选择）", options: { breakLine: true } },
    { text: "公开页抓取、存证、确定性计算（定价 / 审计 / 合规三档核验）", options: { breakLine: true } },
    { text: "视频旁白、内容生成等泛文本工作", options: { breakLine: true } },
    { text: "9 轮评测里 11 次模型选择与规则不一致并被采纳——决策可审计", options: {} },
  ], { x: M + 0.3, y: 2.55, w: 5.3, h: 3.2, fontSize: 13, color: TEXT, paraSpaceAfter: 12, margin: 0, ...FONT_OPTS });
  s.addShape("roundRect", { x: 6.85, y: 1.7, w: 5.9, h: 4.3, fill: { color: WHITE }, rectRadius: 0.08, shadow: { type: "outer", color: "0B2233", blur: 10, offset: 2, angle: 90, opacity: 0.1 } });
  s.addText("本人承担判断", { x: 7.15, y: 1.95, w: 5.3, h: 0.45, fontSize: 18, color: ACCENT, bold: true, margin: 0, ...FONT_OPTS });
  s.addText([
    { text: "产品定义、任务目标设定、结果审核与合规终审", options: { breakLine: true } },
    { text: "人工基线掐表（65.4 分钟）与 30 条事实抽检的逐条回访", options: { breakLine: true } },
    { text: "用工程化补人力：621 单测、漂移门禁、CI 自动部署、墙钟预算", options: { breakLine: true } },
    { text: "AI 未替代：目标设定、审核、终审与抽检——全部由本人完成", options: {} },
  ], { x: 7.15, y: 2.55, w: 5.3, h: 3.2, fontSize: 13, color: TEXT, paraSpaceAfter: 12, margin: 0, ...FONT_OPTS });
  s.addText("1 人参赛（OPC），不临时组队；产品本身就是为单人设计的。", { x: M, y: 6.25, w: 12.1, h: 0.45, fontSize: 15, color: PRIMARY, bold: true, margin: 0, ...FONT_OPTS });
  sourceLine(s, "");
}

// ============ 14. 路线 ============
{
  const s = pres.addSlide();
  s.background = { color: BG_LIGHT };
  mkTitle(s, "商业模式 30% · 落地", "下一步：从报名到总决赛");
  const stops = [
    ["11-23", "完成报名与作品提交", "四件套 + 90 秒视频 + 公网 Demo（截止前完成并实时复核）"],
    ["11-26", "开幕式 · 分组排序 · 材料测试", "14:00 海口经济学院工程楼 F101；演示环境在测试窗口跑通，现场不改代码"],
    ["11-27", "城市赛路演 13 分钟", "10 分钟讲 + 3 分钟问，三区并行综合排序，目标前 10 晋级"],
    ["12-05/06", "全国总决赛 · 海南数据谷", "对接订单、投资人与品牌曝光；接入官方授权的真实 USD 竞品带"],
  ];
  stops.forEach((st, i) => {
    const y = 1.8 + i * 1.2;
    s.addShape("oval", { x: M, y: y + 0.12, w: 0.55, h: 0.55, fill: { color: i === 0 ? ACCENT : PRIMARY } });
    s.addText(st[0], { x: M, y: y + 0.12, w: 0.55, h: 0.55, fontSize: 9.5, color: WHITE, bold: true, align: "center", valign: "middle", margin: 0, ...FONT_OPTS });
    s.addText(st[1], { x: M + 0.85, y, w: 4.5, h: 0.4, fontSize: 15, color: TEXT, bold: true, margin: 0, ...FONT_OPTS });
    s.addText(st[2], { x: M + 0.85, y: y + 0.42, w: 11.3, h: 0.6, fontSize: 12, color: MUTED, margin: 0, ...FONT_OPTS });
    if (i < stops.length - 1) {
      s.addShape("rect", { x: M + 0.24, y: y + 0.72, w: 0.06, h: 0.5, fill: { color: "C9D6DE" } });
    }
  });
  s.addText("路演材料已按组委会要求提交；每组 13 分钟（10 讲 + 3 问），三区并行，综合排序。", { x: M, y: 6.6, w: 12.1, h: 0.4, fontSize: 12, color: MUTED, margin: 0, ...FONT_OPTS });
  sourceLine(s, "");
}

// ============ 15. 结束（深色） ============
{
  const s = pres.addSlide();
  s.background = { color: BG_DARK };
  if (SHOT_DASHBOARD) {
    s.addImage({ path: SHOT_DASHBOARD, x: 0, y: 0, w: 13.33, h: 7.5, sizing: { type: "cover", w: 13.33, h: 7.5 } });
    s.addShape("rect", { x: 0, y: 0, w: 13.33, h: 7.5, fill: { color: BG_DARK, transparency: 30 } });
  }
  s.addText("一个人 + AI", { x: M, y: 1.9, w: 12, h: 1.0, fontSize: 56, color: WHITE, bold: true, margin: 0, ...FONT_OPTS });
  s.addText("也能拥有一支跨境运营部", { x: M, y: 3.0, w: 12, h: 0.8, fontSize: 34, color: ACCENT, bold: true, margin: 0, ...FONT_OPTS });
  s.addText("TradePilot，和 AI 一起，把想法变成生意。谢谢。", { x: M, y: 4.0, w: 12, h: 0.6, fontSize: 20, color: "D9E7EC", margin: 0, ...FONT_OPTS });
  s.addText("现场可演示：https://gsym236998-tradepilot-opc.ms.show", { x: M, y: 5.6, w: 12, h: 0.5, fontSize: 16, color: WHITE, bold: true, margin: 0, ...FONT_OPTS });
  s.addText("商业生产力赛道 · 独立开发者 冯亦根", { x: M, y: 6.2, w: 12, h: 0.4, fontSize: 14, color: "9FB8C7", margin: 0, ...FONT_OPTS });
}

// ============ 16. 备答 Q&A ============
{
  const s = pres.addSlide();
  s.background = { color: BG_LIGHT };
  mkTitle(s, "答辩准备 · Q&A", "备答：高频 5 问（3 分钟问答）");
  const qa = [
    ["Q1 商业模式", "¥199 怎么定的？成本结构？", "市场对标拍板（Shopify Basic 年付约 ¥199）；成本 = LLM token 真实记账 + 抓取算力 + 抽检人工，毛利率待网关单价录入后核算"],
    ["Q2 用户价值", "真实用户是谁？有访谈 / 订单吗？", "一人公司跨境卖家；访谈与订单如实说没有，不编造——当前阶段验证价值主张"],
    ["Q3 场景落地", "断网怎么办？价格怎么回访？", "三档预案（线上 → 本地 → 回放 → 离线视频）；「存证#N」现场回访原页"],
    ["Q4 技术实现", "LLM 是真的在决策吗？", "11 次 llm_override + 决策卡候选集可审计；不拿 llm_planned_rate 当自主证据"],
    ["Q5 表达", "一句话讲清作品？", "一个人 + AI = 一支跨境运营部；我负责想法和决策，AI 负责把想法变成生意"],
  ];
  qa.forEach((q, i) => {
    const y = 1.7 + i * 1.02;
    s.addText(q[0], { x: M, y, w: 2.0, h: 0.35, fontSize: 13, color: PRIMARY, bold: true, margin: 0, ...FONT_OPTS });
    s.addText(q[1], { x: M + 2.1, y, w: 4.0, h: 0.35, fontSize: 12, color: MUTED, margin: 0, ...FONT_OPTS });
    s.addText(q[2], { x: M + 2.1, y: y + 0.34, w: 10.4, h: 0.55, fontSize: 12, color: TEXT, margin: 0, ...FONT_OPTS });
    if (i < qa.length - 1) s.addShape("rect", { x: M, y: y + 0.92, w: 12.1, h: 0.01, fill: { color: "D8E2E8" } });
  });
  s.addText("完整 12 问备答见《BP+路演三件套》第 3 节；Q1 数据口径质疑务必脱稿答好。", { x: M, y: 6.9, w: 12.1, h: 0.35, fontSize: 11, color: MUTED, margin: 0, ...FONT_OPTS });
}

pres.writeFile({ fileName: path.join(OUT_DIR, "路演Deck_TradePilot_OPC海南城市赛.pptx") })
  .then(() => console.log("✅ Deck 已生成：", path.join(OUT_DIR, "路演Deck_TradePilot_OPC海南城市赛.pptx")));
