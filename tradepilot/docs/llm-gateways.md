# LLM 网关实测矩阵与切换方法

> 实测日期：2026-11-21。应用只接一个 OpenAI 兼容网关（`app/config.py` 的
> `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL`），本文件记录当时 5 家网关的
> 实测结论，供线上降级/切换时参考。**密钥永不入库**：本地在 `tradepilot/.llm_key`
> （gitignore），原件在各供应商 key 文件里；线上在创空间 Settings → 环境变量（Secret）。

## 实测结论

| 网关 | LLM_BASE_URL | LLM_MODEL | 结论 |
|---|---|---|---|
| 书生 Atria（主用） | `https://discovery-api.intern-ai.org.cn/v1` | `Atria-Dawn` | ✅ 两个 key 均有效；reasoning_content 与 content 分离干净，`llm.py` 的 JSON 兜底正是按此行为设计 |
| 美团 LongCat | `https://api.longcat.chat/openai/v1` | `LongCat-2.5` | ✅ 回复干净；base **必须带 /v1**，否则 404 |
| 讯飞 MaaS | `https://maas-api.cn-huabei-1.xf-yun.com/v2` | `xopglm53` | ✅ 可用；endpoint 本身已含 `/chat/completions`（llm.py 拼 URL 恰好成立）。`spark-x2.6` 会把推理文本混进 content，JSON 任务优先 `xopglm53` |
| 商汤 Sensenova | `https://token.sensenova.cn/v1` | `sensenova-6.8-flash` | ✅ key 有效，但 tpm/rpm 限流紧（实测 429），不适合批量任务 |
| 云知声 MaaS | `https://maas-api.unisound.com/v1` | — | ❌ 账户欠费（`insufficient_quota`），充值前全部模型 403 |

## 判据与复测方法

最小 chat 调用：prompt 要求「输出严格 JSON：`{"ok":true}`，不要输出其他文字」，
`max_tokens=128`。通过标准：HTTP 200 且 content 可解析。任何网关切换后先跑这个
再上量——商汤的限流、云知声的欠费、讯飞的 content 混推理都是这么测出来的。

## 主用与切换

- **主用书生 Atria**：行为与本地评测口径一致（G6 人工基线在该网关下取得），代码
  对 Atria-Dawn 的 reasoning 兜底经过实战。本地只需 `.llm_key`；线上只设一个
  环境变量 `LLM_API_KEY`，其余走默认。
- **切换备选**：在创空间 Settings → 环境变量里同时改
  `LLM_BASE_URL` + `LLM_MODEL`（+ 必要时 `LLM_API_KEY`）→ 重启空间 →
  `/api/health` 返回 `"llm": true` 即生效。

## 安全约定

- key 不进仓库、文档、聊天记录与 CI 日志；`rsync` 同步清单显式排除 `.llm_key`/`.env`。
- 2026-11-21 实测时书生**旧 key（sk-0fec1 尾 5b52）与新 key（sk-20a45 尾 5570）均有效**；
  旧 key 已列入 `scripts/retire_keys.py` 待退役清单（关联 A5 的 U-1 阻塞项），
  退役前先确认新 key 已配置到本地与创空间两处。
