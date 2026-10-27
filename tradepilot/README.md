# 跨境领航员 TradePilot

> 跨境电商全链路智能运营 OPC 智能体 —— 第二届全国人工智能应用创新大赛 · 通用赛道（AI Agent 赋能千行百业 · 出海）参赛作品（开发中）

## 一句话介绍

给一人公司（OPC）用的跨境电商"AI 运营部"：输入一个目标（如"把宠物用品切入美国站"），
智能体自主完成 **竞品实采 → 定价沙盘 → 合规体检 → Listing 工坊** 全链路，
全程真实数据、决策可解释、结果可量化。

## 三条红线（信任底座）

1. **真实数据**：所有价格来自 Walmart 等公开页的真实抓取（Playwright 无头浏览器），
   每个数据点带来源 URL + 抓取时间戳存证（SQLite，页面可点溯源）；解析不出真实数据
   就报错，绝不编造。
2. **来源闸门**：定价/Listing 等下游工具只允许消费存证在册的数据点
   （`provenance_id` 过闸），编造的 id 一律拦截（held）。
3. **诚实口径**：规则性知识（合规清单、平台佣金）与抓取事实分列标注；
   量化结论一律"以 X 代价换 Y 收益"框架，禁"显著提升"类无口径表述。

## 系统组成

```
app/
├── main.py               FastAPI + WebSocket（决策卡流式遥测）
├── orchestrator.py       任务编排：LLM 规划 + 规则兜底 + 决策卡实录
├── provenance.py         来源存证库（SQLite）
├── config.py             全环境变量配置（LLM Key 不入库）
├── llm.py                LLM 网关（智谱 GLM；无 Key 优雅降级）
└── tools/
    ├── executor.py       统一执行器 + 工具白名单 + 来源闸门
    ├── fetch.py          双通道抓取（httpx 快路径 / Playwright 强路径 + 限速）
    ├── competitor.py     竞品实采（真实解析，宽容多级，拒绝合成）
    ├── pricing.py        定价沙盘（确定性计算：到岸成本/报价下限/敏感性）
    ├── compliance.py     合规体检（官方来源知识库 + 现场验证）
    └── listing.py        Listing 工坊（LLM 组织文案 / 模板兜底，事实回填）
web/                      岸基运营台（原生 Canvas 风格，无构建依赖）
```

## 快速开始

```bash
pip install -r requirements.txt
python -m playwright install chromium
uvicorn app.main:app --host 127.0.0.1 --port 7860
# 打开 http://127.0.0.1:7860
```

可选增强（无 Key 也能完整跑通，走规则流水线）：

```bash
set LLM_API_KEY=你的智谱Key        # 规划与 Listing 文案升级为 LLM
set LLM_MODEL=glm-4-flash
```

## 任务流（一次任务 = 4 张决策卡 + 一份可溯源报告）

| 决策 | 工具 | 产出 |
|---|---|---|
| 1 | /competitor_scan | 真实竞品卡（价格带 min/median/max，来源存证） |
| 2 | /pricing_sandbox | 到岸成本分解、报价下限、5 档净利率敏感性 |
| 3 | /compliance_check | 品类合规清单，逐条对官方页面现场验证（FDA/CPSC/CBP…） |
| 4 | /listing_draft | 英文标题+卖点（LLM 组织/模板兜底，数字服务端回填） |

## 免责说明

竞品价格与页面内容来自公开网页抓取，仅用于市场调研演示；合规清单为编辑性知识
（官方来源可回访），不构成法律意见。平台费率、关税等以参数形式提供，可按实际
站点调整。
