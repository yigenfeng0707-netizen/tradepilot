/* TradePilot 大屏逻辑：任务下发（WS）→ 决策卡流式渲染 → 结果面板服务端回填 */
"use strict";

const $ = (id) => document.getElementById(id);

function el(tag, cls, html) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (html !== undefined) node.innerHTML = html;
  return node;
}

async function checkHealth() {
  try {
    const resp = await fetch("/api/health");
    const data = await resp.json();
    const prov = data.provenance || {};
    $("health").textContent = `服务正常 · 来源存证 ${prov.total || 0} 条 / ${prov.distinct_sources || 0} 个来源`;
    $("health").className = "health ok";
    $("prov-count").textContent = `来源存证 ${prov.total || 0}`;
    return data;
  } catch (e) {
    $("health").textContent = "服务不可达";
    $("health").className = "health";
    return null;
  }
}

function addDecision(card) {
  const box = $("decisions");
  if (box.querySelector(".empty")) box.innerHTML = "";
  const node = el("div", `card ${card.status}`);
  const tryTag = card.attempt > 1 ? ` <span class="retry">第 ${card.attempt} 次尝试</span>` : "";
  node.appendChild(el("div", "head", `决策 ${card.n}：${card.decision}${tryTag}`));
  node.appendChild(el("div", "reason", card.reason || ""));
  node.appendChild(el("div", "summary", card.summary || ""));
  // 候选集：把"为什么不是它"摆出来，自主性才可审计
  const cands = (card.candidates || []).filter((c) => c.name !== card.tool);
  if (cands.length) {
    const line = cands.slice(0, 4).map((c) =>
      `${c.name}(${c.score}${c.eligible ? "" : "·不可执行"})`).join("　");
    node.appendChild(el("div", "cands", `未选候选：${line}`));
  }
  const meta = `状态 ${card.status}${card.gate ? " · 闸门 " + card.gate : ""} · ${card.elapsed_s}s`;
  node.appendChild(el("div", "meta", meta));
  box.appendChild(node);
  box.scrollTop = box.scrollHeight;
}

function renderCompetitors(items) {
  const tbody = $("competitors").querySelector("tbody");
  tbody.innerHTML = "";
  const sym = items[0] && items[0].currency === "CNY" ? "¥" : "US$";
  items.forEach((item, i) => {
    const tr = el("tr");
    tr.appendChild(el("td", null, String(i + 1)));
    tr.appendChild(el("td", null, (item.title || "").replace(/</g, "&lt;")));
    tr.appendChild(el("td", "price", `${sym}${Number(item.price).toFixed(2)}`));
    const td = el("td");
    const link = el("a", null, "存证#" + item.provenance_id);
    link.href = item.source_url; link.target = "_blank"; link.title = item.source_url;
    td.appendChild(link);
    tr.appendChild(td);
    tbody.appendChild(tr);
  });
}

function renderPricing(data) {
  const box = $("pricing");
  box.className = "kv";
  const cb = data.cost_breakdown, band = data.price_band;
  const rows = [
    ["现场汇率", data.fx_rate ? `USD/CNY = <b>${data.fx_rate.toFixed(4)}</b>（存证#${data.fx_provenance_id}）` : "—"],
    ["采购成本", `¥${data.cost_basis_cny.toFixed(2)} ≈ $${data.unit_cost_usd.toFixed(2)}（${data.cost_basis_note}）`],
    ["到岸成本", `$${data.landed_cost.toFixed(2)}（采购 $${cb.unit_cost_usd} + 头程 $${cb.freight_usd} + 关税 $${cb.duty_usd} + 尾程 $${cb.last_mile_usd}）`],
    ["对照价格带", `${band.min.toFixed(2)} – ${band.median.toFixed(2)} – ${band.max.toFixed(2)}　<b>${data.band_source}</b>`],
    ["报价下限", `<b>$${data.floor_price.toFixed(2)}</b>`],
    ["建议", data.suggestion.positioning],
  ];
  box.innerHTML = rows.map(([k, v]) => `<div class="row"><span>${k}</span><span>${v}</span></div>`).join("")
    + `<div class="row"><span>敏感性（净利率 → 保本价）</span><span>${
      data.sensitivity.map(s => `${s.margin_pct}%→$${s.floor_price.toFixed(2)}`).join("　")}</span></div>`
    + `<div class="note" style="margin-top:6px">${data.band_currency_note}。${data.assumptions}</div>`;
}

function renderCompliance(data) {
  const box = $("compliance");
  box.className = "";
  box.innerHTML = `<div class="kv"><div class="row"><span>品类</span><span><b>${data.category}</b> · ${data.live_verified}/${data.total} 条来源现场验证</span></div></div>`
    + data.checks.map((c) => {
      const badge = c.live
        ? `<span class="badge ok">来源有效 HTTP ${c.http_status}</span>`
        : `<span class="badge off">引用标注 · 当前网络不可达</span>`;
      const kind = c.kind === "platform" ? "平台规则" : "官方权威";
      return `<div class="check"><div>${c.item} ${badge}</div>
        <div class="src">[${kind}] ${c.authority} · <a href="${c.source_url}" target="_blank">${c.source_url}</a>${c.provenance_id ? " · 存证#" + c.provenance_id : ""}</div></div>`;
    }).join("");
}

function renderKeywords(data) {
  const box = $("keywords");
  box.className = "";
  const b = data.buckets || {};
  const group = (label, arr) => (arr && arr.length)
    ? `<div class="kwgroup"><span class="kwlabel">${label}</span>${
        arr.map((x) => `<span class="kw" title="出现 ${x.count} 次 · 覆盖率 ${(x.coverage * 100).toFixed(0)}% · 证据：${escapeHtml(x.evidence)}">${
          escapeHtml((x.english || [])[0] || x.observed)}<i>${x.count}</i></span>`).join("")}</div>`
    : "";
  const proxy = data.competition_proxy || {};
  box.innerHTML =
    `<div class="note">样本 ${proxy.sample_size} 条真实竞品标题 · 核心词覆盖率 ${((proxy.core_term_coverage || 0) * 100).toFixed(0)}%`
    + ` · 标题均长 ${proxy.avg_title_len} 字 · 观测词 ${proxy.distinct_terms} 个（未接入搜索量数据源，不编造搜索量）</div>`
    + group("核心", b.core) + group("属性", b.attribute)
    + group("场景", b.scenario) + group("人群", b.audience)
    + group("相关品类", b.related)
    + `<div class="stbox"><b>后台 Search Terms</b>（${data.search_terms_bytes}/250 字节）
        <div class="sttext">${escapeHtml(data.search_terms || "（无可用词）")}</div></div>`;
}

function renderListing(data) {
  const box = $("listing");
  box.className = "";
  const copy = data.copy;
  box.innerHTML = `<div class="kv"><div class="row"><span>生成方式</span><span>${data.generated_by}</span></div></div>
    <div class="copy"><b>Title:</b> ${(copy.title || "").replace(/</g, "&lt;")}
    <ul>${(copy.bullets || []).map((b) => `<li>${String(b).replace(/</g, "&lt;")}</li>`).join("")}</ul></div>`;
}

function renderAudit(data) {
  const box = $("audit");
  box.className = "";
  const cls = data.score >= 85 ? "ok" : data.score >= 70 ? "mid" : "bad";
  const issues = (data.issues || []).slice(0, 12).map((i) => {
    const lv = i.level === "critical" ? "必改" : i.level === "warning" ? "建议" : "提示";
    return `<div class="issue ${i.level}"><b>[${lv}] ${i.rule}</b>
      <span class="ev">${escapeHtml(i.evidence)}</span>
      <div class="fix">→ ${escapeHtml(i.fix)}</div></div>`;
  }).join("");
  box.innerHTML = `
    <div class="scorebar">
      <div class="score ${cls}">${data.score}<span>/100</span></div>
      <div class="verdict">
        <b>${data.grade} 级 · ${data.verdict}</b>
        <div class="note">${data.marketplace} 站 · 必改 ${data.counts.critical} / 建议 ${data.counts.warning} / 提示 ${data.counts.info}
        · 标题 ${data.stats.title_chars}/${data.limits.title} 字符 · 卖点 ${data.stats.bullet_count}/${data.limits.bullets} 条</div>
      </div>
    </div>${issues || '<div class="empty">未发现规则命中</div>'}
    <div class="note">${escapeHtml(data.basis || "")}</div>`;
}

function escapeHtml(s) {
  return String(s == null ? "" : s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function handleEvent(event) {
  if (event.type === "mission_start") {
    $("engine-note").textContent = `规划引擎：${event.plan_engine === "llm" ? "LLM 规划（规则兜底）" : "规则流水线（未配置 LLM）"} · 任务 ${event.mission_id}`;
  } else if (event.type === "decision") {
    addDecision(event);
  } else if (event.type === "mission_done") {
    const report = event.report;
    const scan = report.results.market_scan;
    if (scan && scan.status === "done") renderCompetitors(scan.data.items);
    const pricing = report.results.pricing_sandbox;
    if (pricing && pricing.status === "done") renderPricing(pricing.data);
    const compliance = report.results.compliance_check;
    if (compliance && compliance.status === "done") renderCompliance(compliance.data);
    const kw = report.results.keyword_plan;
    if (kw && kw.status === "done") renderKeywords(kw.data);
    const listing = report.results.listing_draft;
    if (listing && listing.status === "done") renderListing(listing.data);
    const audit = report.results.listing_audit;
    if (audit && audit.status === "done") renderAudit(audit.data);
    const auto = report.autonomy || {};
    $("engine-note").textContent =
      `执行 ${auto.steps_executed || 0} 步 · 重试 ${auto.retries || 0} 次`
      + ` · LLM 决策 ${auto.llm_decisions || 0} 次（覆盖规则序 ${auto.llm_overrides || 0} 次）`
      + ` · 降级 ${(auto.steps_degraded || []).join("/") || "无"}`
      + (report.archive_path ? ` · 已存档 ${report.mission_id}` : "");
    if (report.archive_path) {
      $("export-links").innerHTML =
        `<a href="/api/missions/${report.mission_id}/export?format=md" download>下载上架包 .md</a>`
        + ` ｜ <a href="/api/missions/${report.mission_id}/export?format=json" download>原始报告 .json</a>`
        + ` ｜ <a href="/api/missions/${report.mission_id}" target="_blank">查看存档</a>`;
    }
    checkHealth();
    $("run").disabled = false;
  } else if (event.type === "error") {
    addDecision({ n: "!", decision: "任务异常", reason: "", summary: event.summary, status: "error", elapsed_s: "-" });
    $("run").disabled = false;
  }
}

function runMission() {
  $("run").disabled = true;
  $("decisions").innerHTML = "";
  $("export-links").innerHTML = "";
  ["pricing", "compliance", "keywords", "listing", "audit"].forEach((id) => {
    const box = $(id); box.className = "empty"; box.textContent = "待任务执行";
  });
  const payload = {
    goal: $("goal").value.trim(),
    keyword: $("keyword").value.trim(),
    keyword_cn: $("keyword_cn").value.trim(),
    source: $("source").value,
    category: $("category").value,
    marketplace: $("marketplace").value,
    unit_cost_cny: parseFloat($("unit_cost_cny").value) || null,
    freight_usd: parseFloat($("freight").value) || 2.5,
    duty_pct: parseFloat($("duty").value) || 7.0,
    last_mile_usd: parseFloat($("lastmile").value) || 5.5,
  };
  const proto = location.protocol === "https:" ? "wss://" : "ws://";
  const ws = new WebSocket(proto + location.host + "/ws/mission");
  ws.onmessage = (msg) => { try { handleEvent(JSON.parse(msg.data)); } catch (e) { console.error(e); } };
  ws.onopen = () => ws.send(JSON.stringify(payload));
  ws.onerror = () => { handleEvent({ type: "error", summary: "WebSocket 连接失败" }); };
}

$("run").addEventListener("click", runMission);
checkHealth();
setInterval(checkHealth, 15000);
