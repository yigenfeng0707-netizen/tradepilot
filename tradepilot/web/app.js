/* TradePilot 大屏逻辑：任务下发（WS）→ 决策卡流式渲染 → 服务端回填结果 → 原生 Canvas 2D 绘图
 *
 * 反幻觉红线（本文件所有绘图函数共同遵守）：
 *   1. 任何数据点都必须来自 report.results.*.data 里真实存在的字段，前端不做任何估算/插值/默认示例值；
 *   2. 字段缺失、为空、该步未执行或执行失败 → 画"无数据"占位并写明原因，绝不画示意曲线；
 *   3. 数字全部原样展示后端返回值（仅做小数位格式化），坐标轴范围由数据推导。
 */
"use strict";

const $ = (id) => document.getElementById(id);

function el(tag, cls, html) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (html !== undefined) node.innerHTML = html;
  return node;
}

function escapeHtml(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/* esc：转义 + 空值占位。计数类字段为 null 时显示 "—"，不能显示成 "null" 或空。 */
function esc(v) { return escapeHtml(v === null || v === undefined ? "—" : v); }

function num(v) {
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

function fixed(v, digits) {
  const n = num(v);
  return n === null ? "—" : n.toFixed(digits === undefined ? 2 : digits);
}

/* ---------------- 主题色（与 style.css 保持一致） ---------------- */
const C = {
  accent: "#37dcf2", good: "#3ecf8e", warn: "#e6b450", bad: "#e06c6c",
  text: "#d7e3ee", dim: "#7f93a8", line: "#1d3145", panel: "#0d1a29", grid: "#16283a",
};
const PALETTE = ["#37dcf2", "#3ecf8e", "#e6b450", "#a78bfa", "#f4898c", "#6ea8fe", "#e07be0", "#7ed6a5"];

/* ---------------- 全局状态：最后一帧真实报告 ---------------- */
const STATE = {
  report: null,
  market: null, marketMsg: null,
  pricing: null, pricingMsg: null,
  compliance: null, complianceMsg: null,
  opp: null, oppMsg: null,
  keyword: null, listing: null, audit: null,
};

function resetState() {
  STATE.report = null;
  STATE.market = STATE.pricing = STATE.compliance = STATE.opp = null;
  STATE.keyword = STATE.listing = STATE.audit = null;
  STATE.marketMsg = STATE.pricingMsg = STATE.complianceMsg = STATE.oppMsg = null;
}

/* ---------------- 时间格式化（后端时间戳为 UTC ISO，这里转本地时区展示） ---------------- */
function fmtLocal(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return String(iso);
  const p = (n) => String(n).padStart(2, "0");
  const off = -d.getTimezoneOffset() / 60;
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} `
    + `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}（UTC${off >= 0 ? "+" : ""}${off}）`;
}

/* ---------------- 存证：GET /api/provenance/{id}（唯一新增的前端调用） ---------------- */
const PROV_CACHE = new Map();

function provFetch(id) {
  const key = String(id);
  if (PROV_CACHE.has(key)) return PROV_CACHE.get(key).promise;
  const rec = { state: "loading", data: null, error: null };
  rec.promise = fetch(`/api/provenance/${encodeURIComponent(key)}`)
    .then((r) => {
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return r.json();
    })
    .then((d) => { rec.state = "ok"; rec.data = d; return d; })
    .catch((e) => { rec.state = "err"; rec.error = e.message || String(e); throw e; });
  PROV_CACHE.set(key, rec);
  return rec.promise;
}

/* 「存证#N」链接：点击打开溯源弹层 */
function provLink(id, label) {
  const n = num(id);
  if (n === null) return `<span class="prov-none">无存证</span>`;
  return `<button type="button" class="prov-link" data-prov="${n}" title="查看存证 #${n} 的原始 URL、抓取时间与内容摘要">`
    + `${escapeHtml(label || "存证#" + n)}</button>`;
}

/* 把某个单元格填成该存证的抓取时间（异步回填，失败如实写失败） */
function stampCell(node, id) {
  if (!node) return;
  if (num(id) === null) { node.textContent = "—"; node.className = "ts dim"; return; }
  node.textContent = "读取中…";
  node.className = "ts";
  provFetch(id).then((d) => {
    node.textContent = fmtLocal(d.fetched_at);
    node.title = `存证#${id} · kind=${d.kind} · ${d.url}`;
  }).catch((e) => {
    node.textContent = `存证#${id} 读取失败（${e.message}）`;
    node.className = "ts dim";
  });
}

/* ---------------- Canvas 基础设施：DPR 缩放 / 尺寸自适应 / 统一重绘 ---------------- */
const CHARTS = [];

function mountChart(id, draw) {
  const canvas = $(id);
  if (!canvas) return;
  CHARTS.push({ canvas, draw });
}

let drawScheduled = false;

function redrawAll() {
  if (drawScheduled) return;
  drawScheduled = true;
  const run = () => {
    drawScheduled = false;
    for (const item of CHARTS) {
      try {
        item.draw();
      } catch (err) {  // 退化情形不得让整屏白掉：画错误占位并留痕
        console.error("[chart]", item.canvas.id, err);
        try { drawPlaceholder(item.canvas, "绘图失败", String(err && err.message || err)); } catch (_) { /* 已尽力 */ }
      }
    }
  };
  if (typeof requestAnimationFrame === "function") requestAnimationFrame(run);
  else run();
}

/* 取 2D 上下文并按 devicePixelRatio 缩放：高分屏下线条与文字才不发虚 */
function canvasCtx(canvas) {
  const rect = canvas.getBoundingClientRect();
  const cssW = Math.max(160, Math.round(rect.width || canvas.clientWidth || 320));
  const cssH = Math.max(140, Math.round(rect.height || canvas.clientHeight || 240));
  const dpr = Math.min(Math.max(window.devicePixelRatio || 1, 1), 3);
  const bw = Math.round(cssW * dpr);
  const bh = Math.round(cssH * dpr);
  if (canvas.width !== bw || canvas.height !== bh) { canvas.width = bw; canvas.height = bh; }
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, cssW, cssH);
  ctx.textBaseline = "middle";
  ctx.lineJoin = "round";
  ctx.lineCap = "round";
  return { ctx, w: cssW, h: cssH };
}

function font(ctx, size, weight) {
  ctx.font = `${weight || 400} ${size}px "Microsoft YaHei", "PingFang SC", sans-serif`;
}

function wrapText(ctx, text, maxWidth) {
  const src = String(text == null ? "" : text);
  const out = [];
  let line = "";
  for (const ch of src) {
    const test = line + ch;
    if (ctx.measureText(test).width > maxWidth && line) { out.push(line); line = ch; }
    else line = test;
  }
  if (line) out.push(line);
  return out.slice(0, 6);
}

/* 空数据占位：明确写"无数据"与原因，绝不画示意曲线 */
function drawPlaceholder(canvas, title, reason) {
  const { ctx, w, h } = canvasCtx(canvas);
  ctx.save();
  ctx.setLineDash([4, 4]);
  ctx.strokeStyle = C.line;
  ctx.lineWidth = 1;
  ctx.strokeRect(0.5, 0.5, w - 1, h - 1);
  ctx.restore();

  ctx.textAlign = "center";
  font(ctx, 14, 700);
  ctx.fillStyle = C.dim;
  ctx.fillText(title || "无数据", w / 2, h / 2 - 16);

  font(ctx, 11, 400);
  ctx.fillStyle = "#5f7488";
  const lines = wrapText(ctx, reason || "后端未返回可用字段", w - 48);
  lines.forEach((ln, i) => ctx.fillText(ln, w / 2, h / 2 + 8 + i * 16));

  ctx.textAlign = "left";
  ctx.fillStyle = "#3d5265";
  font(ctx, 10, 400);
  ctx.fillText("canvas 2D · 仅绘制后端返回的真实字段", 10, h - 12);
}
/* =====================================================================
 * 图 1：机会雷达（canvas 2D）
 *   5 轴 = opportunity_scan.candidates[].components 的 yield/depth/spread/noise/terms
 *   取值域 0–100（后端原值，前端不重算、不归一化）
 * ===================================================================== */
const RADAR_AXES = [
  ["yield", "产出 yield"],
  ["depth", "深度 depth"],
  ["spread", "价差 spread"],
  ["noise", "噪声 noise"],
  ["terms", "词覆盖 terms"],
];

function radarSeries() {
  const opp = STATE.opp;
  const cands = opp && Array.isArray(opp.candidates) ? opp.candidates : [];
  const ok = [];
  const skipped = [];
  cands.forEach((c, i) => {
    const comp = c && c.components && typeof c.components === "object" ? c.components : null;
    const raw = comp ? RADAR_AXES.map(([k]) => num(comp[k])) : null;
    if (raw && raw.every((v) => v !== null)) {
      ok.push({
        cand: c,
        vals: raw.map((v) => Math.max(0, Math.min(100, v))),
        color: PALETTE[i % PALETTE.length],
      });
    } else {
      skipped.push((c && (c.keyword_used || c.keyword)) || `候选#${i + 1}`);
    }
  });
  return { cands, ok, skipped };
}

function drawRadar() {
  const canvas = $("cv-radar");
  if (!canvas) return;
  const opp = STATE.opp;
  if (!opp) { drawPlaceholder(canvas, "无数据", STATE.oppMsg || "本次未执行 opportunity_scan"); return; }
  const { ok, skipped } = radarSeries();
  if (!ok.length) {
    drawPlaceholder(canvas, "无数据", skipped.length
      ? `opportunity_scan 返回了 ${skipped.length} 个候选，但 components 五维分量缺失，不猜测补零`
      : "opportunity_scan 已执行但候选为空");
    return;
  }

  const { ctx, w, h } = canvasCtx(canvas);
  const cx = w / 2;
  const cy = h / 2 + 2;
  const R = Math.max(42, Math.min(w / 2 - 86, h / 2 - 42));
  const N = RADAR_AXES.length;
  const ang = (i) => -Math.PI / 2 + (i * 2 * Math.PI) / N;
  const at = (i, v) => [cx + Math.cos(ang(i)) * R * (v / 100), cy + Math.sin(ang(i)) * R * (v / 100)];

  // 网格环
  ctx.strokeStyle = C.grid;
  ctx.lineWidth = 1;
  [20, 40, 60, 80, 100].forEach((level) => {
    ctx.beginPath();
    for (let i = 0; i < N; i += 1) {
      const [x, y] = at(i, level);
      if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    }
    ctx.closePath();
    ctx.strokeStyle = level === 100 ? C.line : C.grid;
    ctx.stroke();
  });

  // 轴辐条
  for (let i = 0; i < N; i += 1) {
    const [x, y] = at(i, 100);
    ctx.beginPath();
    ctx.moveTo(cx, cy);
    ctx.lineTo(x, y);
    ctx.strokeStyle = C.grid;
    ctx.stroke();
  }

  // 刻度值（只标在正上方轴，避免视觉噪声）
  font(ctx, 9, 400);
  ctx.fillStyle = "#3d5265";
  ctx.textAlign = "right";
  [20, 40, 60, 80, 100].forEach((level) => {
    const y = cy - R * (level / 100);
    ctx.fillText(String(level), cx - 4, y);
  });

  // 轴标签
  for (let i = 0; i < N; i += 1) {
    const a = ang(i);
    const lx = cx + Math.cos(a) * (R + 20);
    const ly = cy + Math.sin(a) * (R + 20);
    const cos = Math.cos(a);
    ctx.textAlign = cos > 0.25 ? "left" : cos < -0.25 ? "right" : "center";
    font(ctx, 11, 700);
    ctx.fillStyle = C.dim;
    ctx.fillText(RADAR_AXES[i][1], lx, ly - (Math.abs(Math.sin(a)) < 0.2 ? 0 : 6));
    if (ok.length === 1) {
      font(ctx, 10, 400);
      ctx.fillStyle = ok[0].color;
      ctx.fillText(ok[0].vals[i].toFixed(0), lx, ly + 8);
    }
  }

  // 每个候选一个多边形
  ok.forEach((s, si) => {
    ctx.beginPath();
    for (let i = 0; i < N; i += 1) {
      const [x, y] = at(i, s.vals[i]);
      if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    }
    ctx.closePath();
    ctx.fillStyle = hexA(s.color, si === 0 ? 0.20 : 0.11);
    ctx.fill();
    ctx.strokeStyle = s.color;
    ctx.lineWidth = si === 0 ? 2.2 : 1.4;
    ctx.stroke();

    for (let i = 0; i < N; i += 1) {
      const [x, y] = at(i, s.vals[i]);
      ctx.beginPath();
      ctx.arc(x, y, si === 0 ? 3 : 2.2, 0, Math.PI * 2);
      ctx.fillStyle = s.color;
      ctx.fill();
    }
  });

  ctx.textAlign = "left";
  font(ctx, 10, 400);
  ctx.fillStyle = "#3d5265";
  ctx.fillText(`components 原值 · 0–100 · 候选 ${ok.length} 个${skipped.length ? `（另 ${skipped.length} 个分量缺失未画）` : ""}`, 10, h - 12);
}

/* 十六进制色 → rgba */
function hexA(hex, alpha) {
  const m = /^#([0-9a-f]{6})$/i.exec(String(hex || ""));
  if (!m) return `rgba(55,220,242,${alpha})`;
  const v = parseInt(m[1], 16);
  return `rgba(${(v >> 16) & 255},${(v >> 8) & 255},${v & 255},${alpha})`;
}

/* =====================================================================
 * 图 2：竞品价格带矩阵（canvas 2D）
 *   横轴 = items[].price（真实抓取值），纵轴 = 样本位次（接口返回顺序）
 *   参考线 = price_band.min / median / max（均由后端在同一批真实价格上算得）
 *   点标注 = items[].provenance_id（点开即溯源）
 * ===================================================================== */
function currencySymbol(code) {
  if (code === "CNY") return "¥";
  if (code === "USD") return "$";
  if (code === "EUR") return "€";
  if (code === "GBP") return "£";
  if (code === "JPY") return "¥";
  return code ? `${code} ` : "";
}

function niceStep(raw) {
  if (!(raw > 0)) return 1;
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const norm = raw / mag;
  const mult = norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 5 ? 5 : 10;
  return mult * mag;
}

function drawMarket() {
  const canvas = $("cv-market");
  if (!canvas) return;
  const m = STATE.market;
  if (!m) { drawPlaceholder(canvas, "无数据", STATE.marketMsg || "本次未取得 market_scan 真实商品数据"); return; }

  const items = Array.isArray(m.items) ? m.items : [];
  const pts = items
    .map((it, i) => ({ it, rank: i + 1, price: num(it && it.price) }))
    .filter((p) => p.price !== null);
  if (!pts.length) {
    drawPlaceholder(canvas, "无数据", STATE.marketMsg || "market_scan 返回的商品里没有可解析的 price 字段");
    return;
  }

  const sym = currencySymbol(m.currency);
  const band = m.price_band && typeof m.price_band === "object" ? m.price_band : {};
  const prices = pts.map((p) => p.price);
  let lo = Math.min(...prices);
  let hi = Math.max(...prices);
  const bMin = num(band.min), bMed = num(band.median), bMax = num(band.max);
  if (bMin !== null) lo = Math.min(lo, bMin);
  if (bMax !== null) hi = Math.max(hi, bMax);
  if (!(hi > lo)) {  // 全部同价（退化）：给一个不含猜测的对称窗口
    const pad = Math.max(Math.abs(lo) * 0.1, 1);
    lo -= pad; hi += pad;
  } else {
    const pad = (hi - lo) * 0.12;
    lo -= pad; hi += pad;
  }

  const { ctx, w, h } = canvasCtx(canvas);
  const L = 58, R = 16, T = 26, B = 34;
  const n = pts.length;
  const px = (v) => L + ((v - lo) / (hi - lo)) * (w - L - R);
  const py = (rank) => (n === 1 ? (T + (h - B)) / 2 : h - B - ((rank - 1) / (n - 1)) * (h - B - T));

  // 横轴刻度
  const step = niceStep((hi - lo) / 5);
  const start = Math.ceil(lo / step) * step;
  font(ctx, 10, 400);
  for (let v = start; v <= hi + 1e-9; v += step) {
    const x = px(v);
    ctx.beginPath();
    ctx.moveTo(x, T - 4);
    ctx.lineTo(x, h - B);
    ctx.strokeStyle = C.grid;
    ctx.lineWidth = 1;
    ctx.stroke();
    ctx.fillStyle = C.dim;
    ctx.textAlign = "center";
    ctx.fillText(sym + v.toFixed(step < 1 ? 2 : 0), x, h - B + 14);
  }

  // 纵轴位次
  ctx.textAlign = "right";
  const stepRank = n > 12 ? Math.ceil(n / 8) : 1;
  for (let r = 1; r <= n; r += 1) {
    if (r !== 1 && r !== n && r % stepRank !== 0) continue;
    const y = py(r);
    ctx.beginPath();
    ctx.moveTo(L, y);
    ctx.lineTo(w - R, y);
    ctx.strokeStyle = "rgba(29,49,69,0.65)";
    ctx.stroke();
    ctx.fillStyle = C.dim;
    ctx.fillText(String(r), L - 8, y);
  }
  font(ctx, 10, 700);
  ctx.fillStyle = "#5f7488";
  ctx.textAlign = "left";
  ctx.fillText("样本位次", 8, T - 14);
  ctx.textAlign = "right";
  ctx.fillText(`价格 ${m.currency || ""}`, w - R, h - 8);

  // 三条参考线 + 标签（标签横向避让，避免互相压字）
  const refs = [
    { v: bMin, label: "最低", color: C.good },
    { v: bMed, label: "中位", color: C.accent },
    { v: bMax, label: "最高", color: C.warn },
  ].filter((r) => r.v !== null);
  const labelBoxes = [];
  refs.forEach((ref) => {
    const x = px(ref.v);
    ctx.save();
    ctx.setLineDash([5, 4]);
    ctx.strokeStyle = hexA(ref.color, 0.75);
    ctx.lineWidth = 1.2;
    ctx.beginPath();
    ctx.moveTo(x, T - 4);
    ctx.lineTo(x, h - B);
    ctx.stroke();
    ctx.restore();

    const text = `${ref.label} ${sym}${ref.v.toFixed(2)}`;
    font(ctx, 10, 700);
    const tw = ctx.measureText(text).width;
    let lx = Math.min(Math.max(x - tw / 2, L), w - R - tw);
    labelBoxes.forEach((b) => {
      if (lx < b.x + b.w + 6 && lx + tw + 6 > b.x) lx = b.x + b.w + 6;
    });
    lx = Math.min(lx, w - R - tw);
    labelBoxes.push({ x: lx, w: tw });
    ctx.fillStyle = ref.color;
    ctx.textAlign = "left";
    ctx.fillText(text, lx, T - 12);
  });

  // 散点（每个点 = 一条真实抓取商品，标注其存证号）
  pts.forEach((p) => {
    const x = px(p.price);
    const y = py(p.rank);
    ctx.beginPath();
    ctx.arc(x, y, 4.6, 0, Math.PI * 2);
    ctx.fillStyle = hexA(C.accent, 0.85);
    ctx.fill();
    ctx.strokeStyle = "#0b1622";
    ctx.lineWidth = 1.4;
    ctx.stroke();

    const pid = num(p.it.provenance_id);
    if (pid !== null) {
      font(ctx, 9, 400);
      ctx.fillStyle = "#8aa4ba";
      const label = `#${pid}`;
      const tw = ctx.measureText(label).width;
      ctx.textAlign = "left";
      const lx = x + tw + 8 > w - R ? x - tw - 7 : x + 7;
      ctx.fillText(label, lx, y - 9);
    }
  });

  ctx.textAlign = "left";
  font(ctx, 10, 400);
  ctx.fillStyle = "#3d5265";
  ctx.fillText(`散点 ${n} 条 · 价格取自 items[].price · 参考线取自 price_band`, L, h - 8);
}

/* =====================================================================
 * 图 3：定价沙盘（canvas 2D）
 *   柱 = pricing_sandbox.sensitivity[].margin_pct → floor_price
 *   阴影带 = price_band.min ~ max（对照价格带），虚线 = median 与 floor_price
 * ===================================================================== */
function drawPricing() {
  const canvas = $("cv-pricing");
  if (!canvas) return;
  const d = STATE.pricing;
  if (!d) { drawPlaceholder(canvas, "无数据", STATE.pricingMsg || "本次未执行 pricing_sandbox"); return; }

  const sens = (Array.isArray(d.sensitivity) ? d.sensitivity : [])
    .map((s) => ({ margin: num(s && s.margin_pct), floor: num(s && s.floor_price), raw: s }))
    .filter((s) => s.margin !== null && s.floor !== null);
  if (!sens.length) {
    drawPlaceholder(canvas, "无数据", STATE.pricingMsg || "pricing_sandbox 未返回 sensitivity（净利率→保本价）数据");
    return;
  }

  const band = d.price_band && typeof d.price_band === "object" ? d.price_band : {};
  const bMin = num(band.min), bMed = num(band.median), bMax = num(band.max);
  const floor = num(d.floor_price);
  const hi = Math.max(
    Math.max(...sens.map((s) => s.floor)),
    bMax === null ? 0 : bMax,
    floor === null ? 0 : floor,
    0.01,
  ) * 1.18;

  const { ctx, w, h } = canvasCtx(canvas);
  const L = 54, R = 16, T = 22, B = 34;
  const py = (v) => h - B - (Math.max(0, Math.min(v, hi)) / hi) * (h - B - T);
  const n = sens.length;
  const slot = (w - L - R) / n;
  const bw = Math.min(46, slot * 0.52);

  // 对照价格带区间（后端 band_source 决定它是真实美国站价还是折算参考价，文字见面板明细）
  if (bMin !== null && bMax !== null && bMax > bMin) {
    ctx.fillStyle = "rgba(55,220,242,0.07)";
    ctx.fillRect(L, py(bMax), w - L - R, Math.max(1, py(bMin) - py(bMax)));
    [
      { v: bMin, t: `带下沿 ${bMin.toFixed(2)}`, c: C.accent },
      { v: bMax, t: `带上沿 ${bMax.toFixed(2)}`, c: C.accent },
    ].forEach((l) => {
      ctx.save();
      ctx.setLineDash([4, 4]);
      ctx.strokeStyle = hexA(l.c, 0.5);
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(L, py(l.v));
      ctx.lineTo(w - R, py(l.v));
      ctx.stroke();
      ctx.restore();
      font(ctx, 9, 400);
      ctx.fillStyle = hexA(l.c, 0.95);
      ctx.textAlign = "right";
      ctx.fillText(l.t, w - R, py(l.v) - 7);
    });
  }
  if (bMed !== null) {
    ctx.save();
    ctx.setLineDash([2, 3]);
    ctx.strokeStyle = hexA(C.dim, 0.8);
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(L, py(bMed));
    ctx.lineTo(w - R, py(bMed));
    ctx.stroke();
    ctx.restore();
    font(ctx, 9, 400);
    ctx.fillStyle = C.dim;
    ctx.textAlign = "left";
    ctx.fillText(`带中位 ${bMed.toFixed(2)}`, L + 2, py(bMed) - 7);
  }

  // 柱子
  sens.forEach((s, i) => {
    const cx = L + slot * i + slot / 2;
    const y = py(s.floor);
    const inBand = s.raw && typeof s.raw.in_band === "boolean" ? s.raw.in_band : null;
    const below = s.raw && typeof s.raw.below_band === "boolean" ? s.raw.below_band : null;
    const color = inBand === true ? C.good : below === true ? C.warn : C.accent;

    const grad = ctx.createLinearGradient(0, y, 0, h - B);
    grad.addColorStop(0, hexA(color, 0.85));
    grad.addColorStop(1, hexA(color, 0.16));
    ctx.fillStyle = grad;
    ctx.fillRect(cx - bw / 2, y, bw, h - B - y);
    ctx.strokeStyle = hexA(color, 0.9);
    ctx.lineWidth = 1;
    ctx.strokeRect(cx - bw / 2 + 0.5, y + 0.5, bw - 1, h - B - y - 1);

    font(ctx, 10, 700);
    ctx.fillStyle = color;
    ctx.textAlign = "center";
    ctx.fillText(s.floor.toFixed(2), cx, y - 8);

    font(ctx, 10, 400);
    ctx.fillStyle = C.dim;
    ctx.fillText(`${s.margin % 1 === 0 ? s.margin.toFixed(0) : s.margin}%`, cx, h - B + 14);
  });

  // 报价下限（目标净利率对应的那条线）
  if (floor !== null) {
    ctx.save();
    ctx.setLineDash([7, 5]);
    ctx.strokeStyle = C.bad;
    ctx.lineWidth = 1.6;
    ctx.beginPath();
    ctx.moveTo(L, py(floor));
    ctx.lineTo(w - R, py(floor));
    ctx.stroke();
    ctx.restore();
    font(ctx, 10, 700);
    ctx.fillStyle = C.bad;
    ctx.textAlign = "left";
    ctx.fillText(`报价下限 ${floor.toFixed(2)}`, L + 2, py(floor) - 9);
  }

  // 纵轴
  font(ctx, 10, 400);
  ctx.fillStyle = C.dim;
  ctx.textAlign = "right";
  const stp = niceStep(hi / 4);
  for (let v = 0; v <= hi; v += stp) ctx.fillText(v.toFixed(stp < 1 ? 2 : 0), L - 8, py(v));
  ctx.textAlign = "left";
  font(ctx, 10, 700);
  ctx.fillStyle = "#5f7488";
  ctx.fillText("报价下限（USD/件）", 8, T - 10);
  ctx.textAlign = "right";
  font(ctx, 10, 400);
  ctx.fillStyle = "#3d5265";
  ctx.fillText("净利率", w - R, h - 8);
}
/* =====================================================================
 * 结果面板渲染：每个 render* 都接受"该步的 results 条目"，
 *   null      → 本次未执行（如实写"未执行"，不画东西）
 *   非 done   → 该步失败/被闸门拦下，原样显示后端 summary
 *   done      → 用 data 渲染
 * ===================================================================== */

/* ---- 机会雷达 ---- */
function renderOpportunity(step) {
  const statusBox = $("opp-status");
  const failsBox = $("opp-fails");
  const legendBox = $("opp-legend");
  const noteBox = $("opp-note");
  if (!statusBox) return;

  STATE.opp = null;
  if (!step) {
    STATE.oppMsg = "本次任务未执行 opportunity_scan（mission_done 的 results 中没有该模块）";
  } else if (step.status !== "done" || !step.data) {
    STATE.oppMsg = `opportunity_scan ${step.status || "未完成"}：${step.summary || "后端未返回摘要"}`;
  } else {
    STATE.opp = step.data;
    STATE.oppMsg = null;
  }

  const opp = STATE.opp;
  if (opp) {
    const d = opp;
    const run = num(d.scans_run);
    const thinN = Array.isArray(d.scans_thin) ? d.scans_thin.length : 0;
    const failN = Array.isArray(d.scans_failed) ? d.scans_failed.length : 0;
    const candN = (d.candidates || []).length;
    const ORIGINS = { user: "用户指定", builtin_hypothesis: "内置假设词（非热销榜）" };
    statusBox.innerHTML =
      `<div class="kv-mini">`
      + `<span>数据源 <b>${escapeHtml(d.source || "—")}</b> · 币种 <b>${escapeHtml(d.currency || "—")}</b>`
      + ` · 词源 <b>${escapeHtml(ORIGINS[d.keyword_origin] || d.keyword_origin || "—")}</b></span>`
      + `<span>扫描 <b>${run === null ? "—" : run}</b> ＝ 参与评分 <b>${candN}</b>`
      + ` ＋ 样本过少 <b>${thinN}</b> ＋ 抓取失败 <b>${failN}</b></span>`
      + (run !== null && candN + thinN + failN !== run
        ? `<span class="warn-text">记账不平：三项之和 ${candN + thinN + failN} ≠ 扫描数 ${run}，请核对后端产物</span>` : "")
      + (d.method ? `<span>方法：${escapeHtml(d.method)}</span>` : "")
      + `</div>`;
  } else {
    statusBox.innerHTML = `<div class="kv-mini"><span class="warn-text">${escapeHtml(STATE.oppMsg)}</span></div>`;
  }

  const failsList = opp && Array.isArray(opp.scans_failed) ? opp.scans_failed : [];
  const thinList = opp && Array.isArray(opp.scans_thin) ? opp.scans_thin : [];
  const gapRows = []
    .concat(failsList.map((f) => `<div class="fail"><b>${escapeHtml((f && (f.keyword || f.keyword_used)) || "未知关键词")}</b>`
      + `<span>抓取失败：${escapeHtml((f && f.error) || "后端未给出错误详情")}</span></div>`))
    .concat(thinList.map((t) => `<div class="fail mid"><b>${escapeHtml((t && t.keyword) || "未知关键词")}</b>`
      + `<span>样本过少：${escapeHtml((t && t.reason) || "后端未给出原因")}`
      + `（原始 ${esc(t && t.raw_count)} 条 · 判为噪声 ${esc(t && t.filtered_noise)} 条）</span></div>`));
  failsBox.innerHTML = gapRows.length
    ? gapRows.join("")
    : `<div class="empty">全部候选词均取得可用样本：无抓取失败、无样本过少</div>`;

  const { ok, skipped } = radarSeries();
  legendBox.innerHTML = ok.length
    ? ok.map((s) => {
      const c = s.cand;
      const score = num(c.score);
      const rank = num(c.rank);
      const ids = Array.isArray(c.provenance_ids) ? c.provenance_ids.slice(0, 4) : [];
      return `<span class="lg"><i style="background:${s.color}"></i>`
        + `${escapeHtml(c.keyword_used || c.keyword || "—")}`
        + `${score === null ? "" : ` <b>${score.toFixed(1)}</b>`}`
        + `${rank === null ? "" : ` · 第 ${rank} 名`}`
        + `${num(c.price_min) !== null && num(c.price_max) !== null
          ? ` · ${fixed(c.price_min)}–${fixed(c.price_max)} ${escapeHtml(opp && opp.currency || "")}` : ""}`
        + `${ids.length ? ` · ${ids.map((id) => provLink(id)).join("")}` : ""}</span>`;
    }).join("")
    : `<div class="empty">无可绘制的候选</div>`;

  const bits = [];
  if (skipped.length) bits.push(`分量缺失未绘制：${escapeHtml(skipped.join("、"))}`);
  if (opp && opp.basis) bits.push(`口径：${escapeHtml(opp.basis)}`);
  if (!opp) bits.push("该模块无数据时不画任何示意曲线——雷达轴只接受 components 的 0–100 真实分值。");
  noteBox.innerHTML = bits.join("　");

  drawRadar();
}

/* ---- 竞品价格带矩阵 ---- */
function renderMarket(step) {
  const metaBox = $("market-meta");
  const tbody = $("competitors").querySelector("tbody");
  if (!metaBox || !tbody) return;
  tbody.innerHTML = "";
  STATE.market = null;

  if (!step) {
    STATE.marketMsg = "本次任务未执行 market_scan（无真实商品数据）";
    metaBox.className = "cap";
    metaBox.innerHTML = `<span class="warn-text">${escapeHtml(STATE.marketMsg)}</span>`;
    drawMarket();
    return;
  }
  if (step.status !== "done" || !step.data) {
    STATE.marketMsg = `market_scan ${step.status || "未完成"}：${step.summary || "后端未返回摘要"}`;
    metaBox.className = "cap err";
    metaBox.innerHTML = `<span class="warn-text">${escapeHtml(STATE.marketMsg)}</span>`
      + `<div class="note">该步失败时不会用任何库存示例补位——上表保持为空，图内画"无数据"。</div>`;
    drawMarket();
    return;
  }

  const m = step.data;
  STATE.market = m;
  STATE.marketMsg = null;
  const sym = currencySymbol(m.currency);
  metaBox.className = "cap";
  metaBox.innerHTML =
    `来源 <b>${escapeHtml(m.source || "—")}</b> · 检索词「${escapeHtml(m.keyword_used || "")}」`
    + ` · 币种 <b>${escapeHtml(m.currency || "—")}</b> · 通道 ${escapeHtml(m.channel || "—")}`
    + ` · robots ${escapeHtml(String(m.robots || "—"))}`
    + ` · 剔除噪声 ${num(m.filtered_noise) === null ? "—" : m.filtered_noise} 条`
    + ` · 单品页链接入库 ${num(m.item_url_recorded) === null ? "—" : m.item_url_recorded}/${(m.items || []).length}`
    + `<div class="src-line">搜索页 ${m.source_url ? `<a href="${escapeHtml(m.source_url)}" target="_blank" rel="noopener">${escapeHtml(m.source_url)}</a>` : "—"}`
    + ` · 搜索页${provLink(m.scan_provenance_id)} · 抓取时间 <span class="ts" data-stamp="${num(m.scan_provenance_id) === null ? "" : m.scan_provenance_id}"></span></div>`;

  const items = Array.isArray(m.items) ? m.items : [];
  if (!items.length) {
    tbody.innerHTML = `<tr><td colspan="6" class="empty">market_scan 返回的商品列表为空</td></tr>`;
  }
  items.forEach((it, i) => {
    const tr = el("tr");
    tr.appendChild(el("td", "idx", String(i + 1)));
    tr.appendChild(el("td", "title-cell", escapeHtml(it.title || "（标题缺失）")));
    const priceTd = el("td", "price", `${sym}${fixed(it.price)}`);
    tr.appendChild(priceTd);
    const srcTd = el("td");
    srcTd.innerHTML = it.source_url
      ? `<a href="${escapeHtml(it.source_url)}" target="_blank" rel="noopener">${escapeHtml(it.source || m.source || "来源")}</a>`
      : escapeHtml(it.source || "—");
    tr.appendChild(srcTd);
    const provTd = el("td");
    provTd.innerHTML = provLink(it.provenance_id)
      + (num(it.scan_provenance_id) !== null ? ` <span class="dim-sep">/</span> ${provLink(it.scan_provenance_id, "页#" + it.scan_provenance_id)}` : "");
    tr.appendChild(provTd);
    const tsTd = el("td", "ts", "读取中…");
    tr.appendChild(tsTd);
    tbody.appendChild(tr);
    stampCell(tsTd, it.provenance_id);
  });

  drawMarket();
}

/* ---- 定价沙盘 ---- */
function renderPricing(step) {
  const box = $("pricing");
  if (!box) return;
  STATE.pricing = null;

  if (!step) {
    STATE.pricingMsg = "本次任务未执行 pricing_sandbox（缺少价格带与成本参数）";
    box.className = "kv empty";
    box.innerHTML = `<span class="warn-text">${escapeHtml(STATE.pricingMsg)}</span>`;
    drawPricing();
    return;
  }
  if (step.status !== "done" || !step.data) {
    STATE.pricingMsg = `pricing_sandbox ${step.status || "未完成"}：${step.summary || "后端未返回摘要"}`;
    box.className = "kv";
    box.innerHTML = `<div class="warn-text">${escapeHtml(STATE.pricingMsg)}</div>`;
    drawPricing();
    return;
  }

  const d = step.data;
  STATE.pricing = d;
  STATE.pricingMsg = null;
  const cb = d.cost_breakdown || {};
  const band = d.price_band || {};
  const sens = Array.isArray(d.sensitivity) ? d.sensitivity : [];
  const sym = currencySymbol(band.currency || "USD");

  const rows = [
    ["现场汇率", d.fx_rate
      ? `USD/CNY = <b>${Number(d.fx_rate).toFixed(4)}</b> ${provLink(d.fx_provenance_id, "存证#" + d.fx_provenance_id)}`
      : "—（本次无需折算）"],
    ["采购成本", `¥${fixed(d.cost_basis_cny)} ≈ $${fixed(d.unit_cost_usd)}（${escapeHtml(d.cost_basis_note || "—")}）`],
    ["到岸成本", `$${fixed(d.landed_cost)}（采购 $${fixed(cb.unit_cost_usd)} + 头程 $${fixed(cb.freight_usd)}`
      + ` + 关税 $${fixed(cb.duty_usd)} + 尾程 $${fixed(cb.last_mile_usd)}）`],
    ["对照价格带", `${fixed(band.min)} – ${fixed(band.median)} – ${fixed(band.max)}　<b>${escapeHtml(d.band_source || "—")}</b>`],
    ["报价下限", `<b>$${fixed(d.floor_price)}</b>${d.suggestion && num(d.suggestion.margin_pct) !== null
      ? `（目标净利率 ${d.suggestion.margin_pct}%）` : ""}`],
    ["建议", escapeHtml((d.suggestion && d.suggestion.positioning) || "—")],
  ];
  if (num(cb.commission_pct) !== null) {
    rows.push(["平台佣金 / 关税参数", `${cb.commission_pct}% / ${num(cb.duty_pct) === null ? "—" : cb.duty_pct}%（参数，非事实声明）`]);
  }

  box.className = "kv";
  box.innerHTML = rows.map(([k, v]) => `<div class="row"><span>${k}</span><span>${v}</span></div>`).join("")
    + `<div class="row"><span>敏感性（净利率 → 保本价）</span><span>${sens.length
      ? sens.map((s) => `${s.margin_pct}%→$${fixed(s.floor_price)}`).join("　")
      : "—（后端未返回 sensitivity）"}</span></div>`
    + `<div class="note">${escapeHtml(d.band_currency_note || "")}。${escapeHtml(d.assumptions || "")}</div>`;

  drawPricing();
}

/* ---- 合规体检卡（四态必须区分渲染） ---- */
const VSTATE = {
  clause_matched: { cls: "ok", label: "条款原文已定位" },
  page_only: { cls: "mid", label: "正文已取得 · 未定位条款" },
  blocked: { cls: "bad", label: "HTTP 200 · 内容不可核验" },
  offline: { cls: "off", label: "网络不可达 · 仅引用" },
};

function renderCompliance(step) {
  const statBox = $("comp-stats");
  const box = $("compliance");
  if (!statBox || !box) return;
  STATE.compliance = null;

  if (!step || step.status !== "done" || !step.data) {
    STATE.complianceMsg = !step
      ? "本次任务未执行 compliance_check"
      : `compliance_check ${step.status || "未完成"}：${step.summary || "后端未返回摘要"}`;
    statBox.innerHTML = "";
    box.className = "empty";
    box.innerHTML = `<span class="warn-text">${escapeHtml(STATE.complianceMsg)}</span>`;
    return;
  }

  const d = step.data;
  STATE.compliance = d;
  STATE.complianceMsg = null;
  const checks = Array.isArray(d.checks) ? d.checks : [];
  const countOf = (states) => checks.filter((c) => states.indexOf(c && c.verification) >= 0).length;
  const blockedN = countOf(["blocked"]);
  const offlineN = countOf(["offline"]);

  statBox.innerHTML =
    `<div class="stat"><b>${num(d.live_verified) === null ? countOf(["clause_matched"]) : d.live_verified}</b>`
    + `<span>已定位条款原文</span><i>clause_matched</i></div>`
    + `<div class="stat"><b>${num(d.content_ok_sources) === null ? countOf(["clause_matched", "page_only"]) : d.content_ok_sources}</b>`
    + `<span>内容可核验</span><i>clause_matched + page_only</i></div>`
    + `<div class="stat"><b>${num(d.http_reachable_sources) === null ? checks.filter((c) => c && c.http_reachable).length : d.http_reachable_sources}</b>`
    + `<span>HTTP 可达</span><i>HTTP 200 ≠ 内容可核验</i></div>`
    + `<div class="stat ${blockedN + offlineN ? "warn" : ""}"><b>${blockedN + offlineN}</b>`
    + `<span>不可核验 / 不可达</span><i>拦截页 ${blockedN} · 断网 ${offlineN}</i></div>`
    + `<div class="stat"><b>${num(d.total) === null ? checks.length : d.total}</b><span>条款总数</span>`
    + `<i>品类 ${escapeHtml(d.category || "—")}</i></div>`;

  box.className = "";
  box.innerHTML = (checks.length ? checks : []).map((c) => {
    const vs = VSTATE[c.verification] || { cls: "off", label: "未验证" };
    const kindLabel = c.kind === "platform" ? "平台规则" : "官方权威";
    const page = c.page_url || c.source_url || "";
    const statusTxt = num(c.http_status) === null ? "" : `HTTP ${c.http_status}`;
    const label = c.verification === "blocked" && statusTxt ? `HTTP ${c.http_status} · 内容不可核验` : vs.label;
    const excerpt = c.excerpt ? String(c.excerpt) : "";
    let quote = "";
    if (excerpt) {
      quote = excerpt.length > 180
        ? `<details class="quote"><summary>条款原文片段（${excerpt.length} 字，点击展开）</summary>`
          + `<div class="qbody">「${escapeHtml(excerpt)}」</div></details>`
        : `<div class="quote">「${escapeHtml(excerpt)}」</div>`;
    }
    const terms = Array.isArray(c.matched_terms) && c.matched_terms.length
      ? `<div class="terms">命中词：${c.matched_terms.map((t) => `<span class="term">${escapeHtml(t)}</span>`).join("")}</div>`
      : "";
    const block = c.block_reason
      ? `<div class="block-reason">拦截原因：${escapeHtml(c.block_reason)}</div>` : "";
    const stampId = num(c.provenance_id);
    return `<div class="check">
        <div class="check-top"><span class="check-item">${escapeHtml(c.item || "（条款缺失）")}</span>`
      + `<span class="badge ${vs.cls}">${escapeHtml(label)}</span></div>
        ${quote}${terms}${block}
        <div class="src">[${kindLabel}] ${escapeHtml(c.authority || "—")}`
      + `${page ? ` · <a href="${escapeHtml(page)}" target="_blank" rel="noopener">${escapeHtml(page)}</a>` : ""}`
      + `${statusTxt ? ` · ${statusTxt}` : ""} · ${stampId === null ? `<span class="prov-none">本页不可核验，未落存证</span>` : provLink(stampId)}`
      + ` · 抓取时间 <span class="ts" data-stamp="${stampId === null ? "" : stampId}"></span></div>
      </div>`;
  }).join("") + `<div class="note">${escapeHtml(d.basis || "")}</div>`;

  // 抓取时间戳直接落在每一张卡上（异步回填，失败如实显示）
  box.querySelectorAll("[data-stamp]").forEach((node) => {
    const id = node.getAttribute("data-stamp");
    if (id === "") { node.textContent = "—"; return; }
    stampCell(node, id);
  });
}

/* ---- 关键词层 ---- */
function renderKeywords(step) {
  const box = $("keywords");
  if (!box) return;
  if (!step || step.status !== "done" || !step.data) {
    box.className = "empty";
    box.innerHTML = `<span class="warn-text">${escapeHtml(!step ? "本次任务未执行 keyword_plan"
      : `keyword_plan ${step.status || "未完成"}：${step.summary || "后端未返回摘要"}`)}</span>`;
    return;
  }
  const data = step.data;
  const b = data.buckets || {};
  const group = (label, arr) => (Array.isArray(arr) && arr.length)
    ? `<div class="kwgroup"><span class="kwlabel">${label}</span>${arr.map((x) => {
      const cov = num(x.coverage);
      return `<span class="kw" title="出现 ${x.count} 次 · 覆盖率 ${cov === null ? "—" : (cov * 100).toFixed(0) + "%"} · 证据：${escapeHtml(x.evidence || "")}">`
        + `${escapeHtml((x.english || [])[0] || x.observed || "")}<i>${x.count}</i></span>`;
    }).join("")}</div>`
    : "";
  const proxy = data.competition_proxy || {};
  const cov = num(proxy.core_term_coverage);
  box.className = "";
  box.innerHTML =
    `<div class="note">样本 ${num(proxy.sample_size) === null ? "—" : proxy.sample_size} 条真实竞品标题`
    + ` · 核心词覆盖率 ${cov === null ? "—" : (cov * 100).toFixed(0) + "%"}`
    + ` · 标题均长 ${num(proxy.avg_title_len) === null ? "—" : proxy.avg_title_len} 字`
    + ` · 观测词 ${num(proxy.distinct_terms) === null ? "—" : proxy.distinct_terms} 个`
    + `（未接入搜索量数据源，本工具不产出、也不推算搜索量）</div>`
    + group("核心", b.core) + group("属性", b.attribute)
    + group("场景", b.scenario) + group("人群", b.audience)
    + group("相关品类", b.related)
    + `<div class="stbox"><b>后台 Search Terms</b>（${num(data.search_terms_bytes) === null ? "—" : data.search_terms_bytes}/250 字节）
        <div class="sttext">${escapeHtml(data.search_terms || "（无可用词）")}</div></div>`;
}

/* ---- Listing / 审计 ---- */
function renderListing(step) {
  const box = $("listing");
  const tag = $("listing-tag");
  if (!box) return;
  if (!step || step.status !== "done" || !step.data) {
    box.className = "empty";
    if (tag) tag.textContent = "";
    box.innerHTML = `<span class="warn-text">${escapeHtml(!step ? "本次任务未执行 listing_draft"
      : `listing_draft ${step.status || "未完成"}：${step.summary || "后端未返回摘要"}`)}</span>`;
    return;
  }
  const data = step.data;
  const copy = data.copy || {};
  if (tag) tag.textContent = data.generated_by || "";
  box.className = "";
  box.innerHTML = `<div class="kv"><div class="row"><span>生成方式</span><span>${escapeHtml(data.generated_by || "—")}</span></div>`
    + `<div class="row"><span>事实来源</span><span>${Array.isArray(data.facts_used) ? escapeHtml(data.facts_used.join(" · ")) : escapeHtml(String(data.facts_used || "—"))}</span></div></div>
    <div class="copy"><b>Title：</b>${escapeHtml(copy.title || "")}
    <ul>${(copy.bullets || []).map((x) => `<li>${escapeHtml(String(x))}</li>`).join("")}</ul>
    ${copy.description ? `<div class="desc">${escapeHtml(copy.description)}</div>` : ""}</div>`;
}

function renderAudit(step) {
  const box = $("audit");
  if (!box) return;
  if (!step || step.status !== "done" || !step.data) {
    box.className = "empty";
    box.innerHTML = `<span class="warn-text">${escapeHtml(!step ? "本次任务未执行 listing_audit"
      : `listing_audit ${step.status || "未完成"}：${step.summary || "后端未返回摘要"}`)}</span>`;
    return;
  }
  const d = step.data;
  const score = num(d.score);
  const cls = score === null ? "bad" : score >= 85 ? "ok" : score >= 70 ? "mid" : "bad";
  const counts = d.counts || {};
  const stats = d.stats || {};
  const limits = d.limits || {};
  const issues = (Array.isArray(d.issues) ? d.issues : []).slice(0, 14).map((i) => {
    const lv = i.level === "critical" ? "必改" : i.level === "warning" ? "建议" : "提示";
    return `<div class="issue ${escapeHtml(i.level || "info")}"><b>[${lv}] ${escapeHtml(i.rule || "")}</b>`
      + ` <span class="ev">${escapeHtml(i.evidence || "")}</span>`
      + `<div class="fix">→ ${escapeHtml(i.fix || "")}${i.field ? `（字段 ${escapeHtml(i.field)}）` : ""}</div></div>`;
  }).join("");
  box.className = "";
  box.innerHTML = `
    <div class="scorebar">
      <div class="score ${cls}">${score === null ? "—" : score}<span>/100</span></div>
      <div class="verdict">
        <b>${escapeHtml(d.grade || "—")} 级 · ${escapeHtml(d.verdict || "")}</b>
        <div class="note">${escapeHtml(d.marketplace || "—")} 站 · 必改 ${num(counts.critical) === null ? "—" : counts.critical}`
    + ` / 建议 ${num(counts.warning) === null ? "—" : counts.warning} / 提示 ${num(counts.info) === null ? "—" : counts.info}`
    + ` · 标题 ${num(stats.title_chars) === null ? "—" : stats.title_chars}/${num(limits.title) === null ? "—" : limits.title} 字符`
    + ` · 卖点 ${num(stats.bullet_count) === null ? "—" : stats.bullet_count}/${num(limits.bullets) === null ? "—" : limits.bullets} 条</div>
      </div>
    </div>${issues || '<div class="empty">未发现规则命中</div>'}
    <div class="note">${escapeHtml(d.basis || "")}</div>`;
}
/* =====================================================================
 * 决策时间线：采纳模型决策 vs 越界回落规则，必须一眼可区分
 * ===================================================================== */
function llmPill(card) {
  if (card.llm_adopted) return `<span class="pill llm-yes">采纳模型决策</span>`;
  if (card.llm_rejected) return `<span class="pill llm-no">模型建议越界 · 已回落规则</span>`;
  return `<span class="pill llm-rule">规则评分</span>`;
}

function decisionCard(card) {
  const node = el("div", `card ${card.status || ""}`);
  const head = el("div", "head");
  head.innerHTML = `<span class="n">决策 ${escapeHtml(String(card.n == null ? "?" : card.n))}</span>`
    + `<span class="tool">${escapeHtml(card.tool || card.decision || "")}</span>`
    + `${num(card.attempt) !== null && card.attempt > 1 ? `<span class="retry">第 ${card.attempt} 次尝试</span>` : ""}`
    + llmPill(card);
  node.appendChild(head);
  if (card.reason) node.appendChild(el("div", "reason", escapeHtml(card.reason)));
  if (card.summary) node.appendChild(el("div", "summary", escapeHtml(card.summary)));

  const cands = Array.isArray(card.candidates) ? card.candidates : [];
  if (cands.length) {
    const chips = cands.slice(0, 6).map((c) => {
      const sc = num(c.score);
      return `<span class="cand ${c.eligible ? "" : "no"} ${c.name === card.tool ? "chosen" : ""}">`
        + `${escapeHtml(c.name || "?")}<i>${sc === null ? "—" : sc.toFixed(0)}</i>`
        + `${c.eligible ? "" : "· 不可执行"}</span>`;
    }).join("");
    node.appendChild(el("div", "cands", `候选（规则评分）：${chips}`));
  }

  const keys = Array.isArray(card.input_keys) ? card.input_keys : [];
  const meta = `状态 ${escapeHtml(String(card.status || "—"))}`
    + `${card.gate ? ` · 闸门 ${escapeHtml(String(card.gate))}` : ""}`
    + `${num(card.elapsed_s) === null ? "" : ` · 累计 ${card.elapsed_s}s`}`
    + `${keys.length ? ` · 入参 ${escapeHtml(keys.slice(0, 8).join(", "))}` : ""}`;
  node.appendChild(el("div", "meta", meta));
  return node;
}

function addDecision(card) {
  const box = $("decisions");
  if (!box) return;
  const empty = box.querySelector(".empty");
  if (empty) box.innerHTML = "";
  box.appendChild(decisionCard(card));
  box.scrollTop = box.scrollHeight;
}

function renderDecisions(list) {
  const box = $("decisions");
  if (!box) return;
  box.innerHTML = "";
  if (!list.length) {
    box.innerHTML = `<div class="empty">本次报告未包含决策记录</div>`;
    return;
  }
  list.forEach((c) => box.appendChild(decisionCard(c)));
  box.scrollTop = box.scrollHeight;
}

function renderAutonomy(report) {
  const foot = $("decision-foot");
  const tag = $("decision-tag");
  if (!foot) return;
  const a = report.autonomy || {};
  const int = (v) => (num(v) === null ? "—" : v);
  const adopted = num(a.llm_decisions);

  foot.innerHTML = `<div class="autonomy">`
    + `<span class="a"><b>${int(a.steps_executed)}</b>执行步数</span>`
    + `<span class="a"><b>${int(a.retries)}</b>重试</span>`
    + `<span class="a hi"><b>${int(a.llm_decisions)}</b>LLM 采纳决策</span>`
    + `${num(a.llm_overrides) === null ? "" : `<span class="a"><b>${a.llm_overrides}</b>覆盖规则首选</span>`}`
    + `${num(a.llm_rejected_to_rule) === null ? "" : `<span class="a"><b>${a.llm_rejected_to_rule}</b>越界回落</span>`}`
    + `</div>`
    + `<div class="note">口径：LLM 采纳决策 = 规划引擎的模型选择被真正执行的步数（被越界拦下并回落规则的不计入）`
    + `${adopted === null ? "；本次报告未提供该指标" : `；本次采纳 ${adopted} 步`}。`
    + `降级步骤：${escapeHtml((Array.isArray(a.steps_degraded) && a.steps_degraded.length ? a.steps_degraded.join("、") : "无"))}`
    + `　未执行步骤：${escapeHtml((Array.isArray(a.steps_not_run) && a.steps_not_run.length ? a.steps_not_run.join("、") : "无"))}</div>`;
  if (tag) tag.textContent = `流式 · 采纳 ${int(a.llm_decisions)}`;
}

/* =====================================================================
 * 报告头：任务 ID / 引擎 / 时间戳 / 存证统计
 * ===================================================================== */
function renderHead(report) {
  const meta = $("head-meta");
  const goal = $("head-goal");
  if (!meta) return;
  if (!report || !report.mission_id) {
    meta.textContent = "尚未执行任务";
    if (goal) goal.textContent = "";
    return;
  }
  const prov = report.provenance || {};
  const engine = report.plan_engine === "llm"
    ? `LLM 规划（${escapeHtml(report.llm_model || "模型未回报")}）+ 规则兜底`
    : `规则流水线（未配置 LLM）`;
  meta.innerHTML = `任务 <b>${escapeHtml(report.mission_id)}</b> · 规划引擎 ${engine}`
    + ` · 开始 ${fmtLocal(report.started_at)} · 结束 ${fmtLocal(report.finished_at)}`
    + ` · 耗时 ${num(report.elapsed_s) === null ? "—" : report.elapsed_s}s`
    + ` · 存证 ${num(prov.total) === null ? "—" : prov.total} 条`;
  if (goal) goal.textContent = `目标：${report.goal || "—"}`;
}

/* =====================================================================
 * 全量回填（mission_done）
 * ===================================================================== */
function renderReport(report) {
  const rep = report || {};
  const R = rep.results && typeof rep.results === "object" ? rep.results : {};
  STATE.report = rep;

  renderHead(rep);
  renderOpportunity(R.opportunity_scan || null);
  renderMarket(R.market_scan || null);
  renderPricing(R.pricing_sandbox || null);
  renderCompliance(R.compliance_check || null);
  renderKeywords(R.keyword_plan || null);
  renderListing(R.listing_draft || null);
  renderAudit(R.listing_audit || null);
  renderDecisions(Array.isArray(rep.decisions) ? rep.decisions : []);
  renderAutonomy(rep);
  redrawAll();

  const note = $("engine-note");
  if (note) {
    note.textContent = `任务 ${rep.mission_id || "—"} 完成 · 规划引擎 ${rep.plan_engine === "llm" ? "LLM" : "规则"}`
      + ` · 耗时 ${num(rep.elapsed_s) === null ? "—" : rep.elapsed_s}s`
      + (rep.archive_path ? " · 已存档" : rep.archive_error ? ` · 存档失败：${rep.archive_error}` : "");
  }
  if (rep.archive_path) {
    $("export-links").innerHTML =
      `<a href="/api/missions/${encodeURIComponent(rep.mission_id)}/export?format=md" download>下载上架包 .md</a>`
      + ` ｜ <a href="/api/missions/${encodeURIComponent(rep.mission_id)}/export?format=json" download>原始报告 .json</a>`
      + ` ｜ <a href="/api/missions/${encodeURIComponent(rep.mission_id)}/export?format=docx" download>Word 版 .docx</a>`
      + ` ｜ <a href="/api/missions/${encodeURIComponent(rep.mission_id)}" target="_blank" rel="noopener">查看存档</a>`;
  }
  const runBtn = $("run");
  if (runBtn) runBtn.disabled = false;
  checkHealth();
}

/* =====================================================================
 * 溯源弹层：GET /api/provenance/{id}
 * ===================================================================== */
const PROV_PREFERRED = [
  "title", "price", "currency", "source", "source_url", "search_url", "keyword_used",
  "channel", "raw_count", "kept_count", "filtered_noise", "item", "authority", "kind",
  "verification", "matched_terms", "excerpt", "http_status", "entry_url", "fetch_error",
  "rate", "pair", "date", "base", "quote",
];

function provValueHtml(v) {
  if (v === null || v === undefined) return `<span class="dim">null</span>`;
  if (Array.isArray(v)) {
    const s = v.map((x) => (typeof x === "object" ? JSON.stringify(x) : String(x))).join(" / ");
    return escapeHtml(s.length > 300 ? s.slice(0, 300) + "…" : s || "（空数组）");
  }
  if (typeof v === "object") {
    const s = JSON.stringify(v, null, 0);
    return `<code>${escapeHtml(s.length > 300 ? s.slice(0, 300) + "…" : s)}</code>`;
  }
  const s = String(v);
  if (s.length > 300) {
    return `<details><summary>${escapeHtml(s.slice(0, 120))}…（共 ${s.length} 字）</summary>`
      + `<div class="qbody">${escapeHtml(s)}</div></details>`;
  }
  return escapeHtml(s === "" ? "（空）" : s);
}

function provBodyHtml(d) {
  const payload = d.payload && typeof d.payload === "object" ? d.payload : { payload: d.payload };
  const keys = Object.keys(payload);
  const ordered = PROV_PREFERRED.filter((k) => keys.indexOf(k) >= 0)
    .concat(keys.filter((k) => PROV_PREFERRED.indexOf(k) < 0).sort());
  const rows = ordered.slice(0, 32).map((k) =>
    `<div class="prow"><span class="pk">${escapeHtml(k)}</span><span class="pv">${provValueHtml(payload[k])}</span></div>`
  ).join("");
  return `<div class="pmeta">
      <div class="prow"><span class="pk">存证号</span><span class="pv"><b>#${escapeHtml(String(d.id))}</b></span></div>
      <div class="prow"><span class="pk">类型 kind</span><span class="pv"><b>${escapeHtml(d.kind || "—")}</b></span></div>
      <div class="prow"><span class="pk">原始 URL</span><span class="pv">${d.url
      ? `<a href="${escapeHtml(d.url)}" target="_blank" rel="noopener">${escapeHtml(d.url)}</a>` : "—"}</span></div>
      <div class="prow"><span class="pk">抓取时间（本地）</span><span class="pv">${escapeHtml(fmtLocal(d.fetched_at))}
        <span class="dim">（服务端 UTC：${escapeHtml(d.fetched_at || "—")}）</span></span></div>
      <div class="prow"><span class="pk">内容摘要 hash</span><span class="pv"><code>${escapeHtml(d.content_hash || "—")}</code></span></div>
    </div>
    <div class="psection">payload 关键字段（后端原样，未加工）</div>
    <div class="ppayload">${rows || '<div class="empty">payload 为空</div>'}</div>
    <div class="pactions">${d.url
      ? `<a class="btn-like" href="${escapeHtml(d.url)}" target="_blank" rel="noopener">打开原始页面 ↗</a>` : ""}
      <span class="note">内容 hash 用于事后抽检：同一 URL 再次抓取若 hash 变化，说明页面已改动。</span></div>`;
}

function openProvenance(id) {
  const modal = $("prov-modal");
  if (!modal) return;
  $("prov-title").textContent = `来源存证 #${id}`;
  $("prov-body").innerHTML = `<div class="empty">正在读取 /api/provenance/${escapeHtml(String(id))} …</div>`;
  modal.hidden = false;
  document.body.classList.add("modal-open");
  provFetch(id).then((d) => {
    $("prov-body").innerHTML = provBodyHtml(d);
  }).catch((e) => {
    $("prov-body").innerHTML = `<div class="empty warn-text">读取失败：${escapeHtml(e.message || String(e))}。`
      + `该存证可能尚未落库，或 /api/provenance/${escapeHtml(String(id))} 不可达——不猜测内容。</div>`;
  });
}

function closeProvenance() {
  const modal = $("prov-modal");
  if (!modal) return;
  modal.hidden = true;
  document.body.classList.remove("modal-open");
}

/* =====================================================================
 * 事件入口：mission_start / decision / mission_done / error（协议不变）
 * ===================================================================== */
function handleEvent(event) {
  if (!event || typeof event !== "object" || !event.type) return;
  if (event.type === "mission_start") {
    const note = $("engine-note");
    if (note) {
      note.textContent = `规划引擎：${event.plan_engine === "llm"
        ? `LLM 规划（${event.llm_model || "模型未回报"}）+ 规则兜底` : "规则流水线（未配置 LLM）"}`
        + ` · 任务 ${event.mission_id || "—"}`;
    }
    const head = $("head-meta");
    if (head) head.textContent = `任务 ${event.mission_id || "—"} 运行中 · 开始 ${fmtLocal(event.started_at)}`;
    const tag = $("decision-tag");
    if (tag) tag.textContent = "流式接收中";
  } else if (event.type === "decision") {
    addDecision(event);
  } else if (event.type === "mission_done") {
    renderReport(event.report || {});
  } else if (event.type === "error") {
    addDecision({
      n: "!", tool: "任务异常", decision: "任务异常",
      reason: "", summary: event.summary || "后端返回未说明的错误",
      status: "error", elapsed_s: null,
    });
    const note = $("engine-note");
    if (note) note.textContent = `任务异常：${event.summary || "未知错误"}`;
    const btn = $("run");
    if (btn) btn.disabled = false;
  }
}

/* =====================================================================
 * 任务下发
 * ===================================================================== */
function resetPanels() {
  resetState();
  PROV_CACHE.clear();

  const body = $("competitors").querySelector("tbody");
  if (body) body.innerHTML = "";
  const marketMeta = $("market-meta");
  if (marketMeta) { marketMeta.className = "cap"; marketMeta.textContent = "待任务执行"; }

  const pricing = $("pricing");
  if (pricing) { pricing.className = "kv empty"; pricing.textContent = "待竞品实采后自动计算"; }
  const comp = $("compliance");
  if (comp) { comp.className = "empty"; comp.textContent = "待任务执行"; }
  const compStats = $("comp-stats");
  if (compStats) compStats.innerHTML = "";
  const kw = $("keywords");
  if (kw) { kw.className = "empty"; kw.textContent = "待竞品实采后反推搜索词"; }
  const listing = $("listing");
  if (listing) { listing.className = "empty"; listing.textContent = "待任务执行"; }
  const listingTag = $("listing-tag");
  if (listingTag) listingTag.textContent = "";
  const audit = $("audit");
  if (audit) { audit.className = "empty"; audit.textContent = "待 Listing 成稿后自动审计"; }

  const oppStatus = $("opp-status");
  if (oppStatus) oppStatus.innerHTML = `<div class="kv-mini"><span>待任务执行</span></div>`;
  const oppFails = $("opp-fails");
  if (oppFails) oppFails.innerHTML = `<div class="empty">—</div>`;
  const oppLegend = $("opp-legend");
  if (oppLegend) oppLegend.innerHTML = "";
  const oppNote = $("opp-note");
  if (oppNote) oppNote.textContent = "";

  const headMeta = $("head-meta");
  if (headMeta) headMeta.textContent = "任务执行中…";
  const headGoal = $("head-goal");
  if (headGoal) headGoal.textContent = "";
  const foot = $("decision-foot");
  if (foot) foot.innerHTML = "";
  const decTag = $("decision-tag");
  if (decTag) decTag.textContent = "流式";

  STATE.oppMsg = "本次任务未执行 opportunity_scan";
  STATE.marketMsg = "本次任务未取得 market_scan 真实商品数据";
  STATE.pricingMsg = "本次任务未执行 pricing_sandbox";
  redrawAll();
}

function numOr(input, fallback) {
  const v = input ? parseFloat(input.value) : NaN;
  return Number.isFinite(v) ? v : fallback;
}

function runMission() {
  const runBtn = $("run");
  if (runBtn) runBtn.disabled = true;
  const dec = $("decisions");
  if (dec) dec.innerHTML = "";
  const links = $("export-links");
  if (links) links.innerHTML = "";
  resetPanels();

  const payload = {
    goal: $("goal").value.trim(),
    keyword: $("keyword").value.trim(),
    keyword_cn: $("keyword_cn").value.trim(),
    source: $("source").value,
    category: $("category").value,
    marketplace: $("marketplace").value,
    unit_cost_cny: Number.isFinite(parseFloat($("unit_cost_cny").value)) ? parseFloat($("unit_cost_cny").value) : null,
    freight_usd: numOr($("freight"), 2.5),
    duty_pct: numOr($("duty"), 7.0),
    last_mile_usd: numOr($("lastmile"), 5.5),
    commission_pct: numOr($("commission_pct"), 15.0),
    target_margin_pct: numOr($("target_margin_pct"), 20.0),
    keywords: $("radar_keywords") ? $("radar_keywords").value.trim() : "",
  };

  const proto = location.protocol === "https:" ? "wss://" : "ws://";
  let ws;
  try {
    ws = new WebSocket(proto + location.host + "/ws/mission");
  } catch (e) {
    handleEvent({ type: "error", summary: `WebSocket 无法建立：${e.message}` });
    return;
  }
  ws.onmessage = (msg) => {
    try { handleEvent(JSON.parse(msg.data)); } catch (e) { console.error("[ws-parse]", e); }
  };
  ws.onopen = () => ws.send(JSON.stringify(payload));
  ws.onerror = () => handleEvent({ type: "error", summary: "WebSocket 连接失败（服务是否已启动？）" });
  ws.onclose = () => {
    if (runBtn && runBtn.disabled) {
      runBtn.disabled = false;
      const note = $("engine-note");
      if (note && !STATE.report) note.textContent = "任务通道已关闭，未收到 mission_done——界面保持无数据，不补造结果。";
    }
  };
}

/* =====================================================================
 * 启动：健康检查 / 图表挂载 / 尺寸自适应
 * ===================================================================== */
async function checkHealth() {
  try {
    const resp = await fetch("/api/health");
    const data = await resp.json();
    const prov = data.provenance || {};
    const health = $("health");
    if (health) {
      health.textContent = `服务正常 · 来源存证 ${prov.total || 0} 条 / ${prov.distinct_sources || 0} 个来源`
        + ` · LLM ${data.llm ? (data.llm_model || "已配置") : "未配置（规则流水线）"}`;
      health.className = "health ok";
    }
    const count = $("prov-count");
    if (count) count.textContent = `来源存证 ${prov.total || 0}`;
    return data;
  } catch (e) {
    const health = $("health");
    if (health) { health.textContent = "服务不可达"; health.className = "health"; }
    return null;
  }
}

function boot() {
  const runBtn = $("run");
  if (runBtn) runBtn.addEventListener("click", runMission);

  mountChart("cv-radar", drawRadar);
  mountChart("cv-market", drawMarket);
  mountChart("cv-pricing", drawPricing);

  // 存证链接：事件委托（结果区每次重绘都会产生新的「存证#N」按钮）
  document.addEventListener("click", (ev) => {
    const target = ev.target;
    const btn = target && target.closest ? target.closest("[data-prov]") : null;
    if (btn) { ev.preventDefault(); openProvenance(btn.getAttribute("data-prov")); }
  });
  const closeBtn = $("prov-close");
  if (closeBtn) closeBtn.addEventListener("click", closeProvenance);
  const modal = $("prov-modal");
  if (modal) {
    modal.addEventListener("click", (ev) => { if (ev.target === modal) closeProvenance(); });
  }
  document.addEventListener("keydown", (ev) => { if (ev.key === "Escape") closeProvenance(); });

  // 尺寸变化重绘（容器宽度变化 / 窗口缩放 / DPR 变化导致的 resize 都走这里）
  let lastW = 0;
  let lastDpr = window.devicePixelRatio || 1;
  const onResize = () => {
    const host = $("right");
    const w = host ? host.clientWidth : window.innerWidth;
    const dpr = window.devicePixelRatio || 1;
    if (Math.abs(w - lastW) < 2 && Math.abs(dpr - lastDpr) < 0.01) return;
    lastW = w; lastDpr = dpr;
    redrawAll();
  };
  lastW = $("right") ? $("right").clientWidth : window.innerWidth;
  window.addEventListener("resize", onResize);
  if (typeof ResizeObserver === "function") {
    const host = $("right");
    if (host) new ResizeObserver(onResize).observe(host);
  }

  resetPanels();
  redrawAll();
  checkHealth();
  setInterval(checkHealth, 15000);
}

if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
else boot();


/* =====================================================================
 * 存档回放：把已归档的真实报告灌回同一套渲染管线
 *
 * 为什么必须有：设计说明里写了"证据链归档 + 答辩现场回放"，但 renderReport()
 * 此前只有实时 WebSocket mission_done 一条调用路径——存档取回来也显示不出来，
 * "现场回放"就只是文档上的一句承诺。
 *
 * 诚实要求：回放的是**历史真实抓取结果**（含当时的存证号与时间戳），
 * 界面必须明说这一点，不能让人误当成刚刚采集的数据。
 * ===================================================================== */
let ARCHIVE_LOADED = false;

async function loadArchiveList() {
  const sel = $("replay-select");
  if (!sel) return;
  sel.innerHTML = `<option value="">载入中…</option>`;
  try {
    const res = await fetch("/api/missions?limit=20");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    const list = Array.isArray(data.missions) ? data.missions : [];
    ARCHIVE_LOADED = true;
    if (!list.length) {
      sel.innerHTML = `<option value="">暂无存档（先下达一次任务）</option>`;
      return;
    }
    const opts = list.map((m) => {
      const id = String(m.mission_id || m.id || "");
      if (!id) return "";
      const goal = String(m.goal || "（无目标文本）").slice(0, 24);
      const when = fmtLocal(m.finished_at || m.started_at || "");
      return `<option value="${escapeHtml(id)}">${escapeHtml(when)} · ${escapeHtml(goal)}`
        + ` · ${escapeHtml(id.slice(0, 8))}</option>`;
    }).join("");
    sel.innerHTML = `<option value="">选择存档（${list.length} 条）</option>${opts}`;
  } catch (e) {
    ARCHIVE_LOADED = false;
    sel.innerHTML = `<option value="">存档列表读取失败</option>`;
    const note = $("engine-note");
    if (note) note.textContent = `存档列表读取失败：${(e && e.message) || e}`;
  }
}

async function replayMission() {
  const sel = $("replay-select");
  const note = $("engine-note");
  const id = sel ? sel.value : "";
  if (!id) {
    if (note) note.textContent = "未选择具体存档，界面未改动。";
    return;
  }
  if (note) note.textContent = `正在读取存档 ${id} …`;
  try {
    const res = await fetch(`/api/missions/${encodeURIComponent(id)}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const report = await res.json();
    if (!report || !report.results) throw new Error("存档内容缺少 results 字段");
    resetPanels();
    renderReport(report);
    const prov = (report.provenance || {}).total;
    if (note) {
      note.textContent = `回放存档 ${report.mission_id || id}`
        + ` · 当时耗时 ${num(report.elapsed_s) === null ? "—" : report.elapsed_s}s`
        + ` · 完成于 ${fmtLocal(report.finished_at || "")}`
        + ` · 决策 ${Array.isArray(report.decisions) ? report.decisions.length : 0} 条`
        + ` · 存证 ${num(prov) === null ? "—" : prov} 条`
        + `【历史真实抓取结果，非本次实时采集】`;
    }
  } catch (e) {
    if (note) note.textContent = `存档回放失败：${(e && e.message) || e}`;
  }
}

function initReplay() {
  const sel = $("replay-select");
  const btn = $("replay");
  if (!sel || !btn) return;
  const lazy = () => { if (!ARCHIVE_LOADED) loadArchiveList(); };
  sel.addEventListener("focus", lazy);
  sel.addEventListener("click", lazy);
  btn.addEventListener("click", replayMission);
}

initReplay();
