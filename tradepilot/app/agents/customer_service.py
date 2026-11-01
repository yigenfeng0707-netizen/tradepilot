# Copyright 2026 TradePilot project. Apache-2.0.
"""客服 Agent（M1：从海丝迁入，能力保留、代码精简）。

能力：
- 情绪识别（正面/中性/负面/愤怒）：关键词加权，中英双语；
- 纠纷识别（退款/物流/质量/拒付）：命中即标等级与建议动作；
- FAQ 匹配：品类词表 + 关键词重合度打分，返回 Top 条目；
- 回复生成：LLM 在线时组织语言（只准依据 FAQ 事实，不给编造政策的机会），
  离线降级为模板。

诚实标注：FAQ 是静态样本（data_quality="sample_faq"），LLM 生成回复会带
generated_by="llm"；两者前端都必须明示，不得伪装成官方政策。
会话历史暂存内存（M3 迁 Postgres），重启即丢——文档已注明。
"""
from __future__ import annotations

import re
from typing import Any

from .. import llm

AGENT = "智能客服Agent"

# ---------------------------------------------------------------- 词表
EMOTIONS: dict[str, dict[str, Any]] = {
    "angry": {"label": "愤怒", "weight": 3,
              "words": ("垃圾", "骗子", "投诉", "曝光", "差评", "退款退货", "太差劲",
                        "太差", "气死", "scam", "fraud", "terrible", "disgusting",
                        "refund now")},
    "negative": {"label": "负面", "weight": 2,
                 "words": ("失望", "不满意", "太慢", "破损", "坏了", "退货", "差",
                           "disappointed", "broken", "late", "poor", "bad")},
    "positive": {"label": "正面", "weight": 1,
                 "words": ("谢谢", "很好", "满意", "喜欢", "棒", "不错",
                           "thanks", "great", "good", "love", "perfect")},
}

# 按"根因优先 + 严重度"排序：拒付(最高) > 质量 > 退款 > 物流
# 一条消息常同时命中多个（"质量太差要退款"），先判根因才能给对处理动作
DISPUTES: dict[str, dict[str, Any]] = {
    "chargeback": {"label": "拒付风险", "level": "critical",
                   "words": ("拒付", "chargeback", "dispute", "银行申诉"),
                   "action": "立即整理订单证据链（存证号 + 物流 + 沟通记录）提交平台"},
    "quality": {"label": "质量纠纷", "level": "high",
                "words": ("质量", "破损", "瑕疵", "defective", "damaged", "quality issue"),
                "action": "索要实物照片，按平台规则走质量索赔或换货"},
    "refund": {"label": "退款纠纷", "level": "high",
               "words": ("退款", "退钱", "refund", "money back"),
               "action": "核对订单与物流签收记录，48h 内给出退款方案"},
    "logistics": {"label": "物流纠纷", "level": "medium",
                  "words": ("物流", "没收到", "delay", "not received", "shipping"),
                  "action": "提供物流单号与最新轨迹，超时件主动补发或退款"},
}

FAQ: dict[str, list[dict[str, str]]] = {
    "宠物用品": [
        {"q_zh": "这个产品对猫安全吗？", "a_zh": "产品使用宠物安全材质，已通过国际安全认证，具体认证见商品详情页。",
         "q_en": "Is this product safe for cats?",
         "a_en": "Made from pet-safe materials with international safety certification; see the listing for details."},
        {"q_zh": "自动喂食器如何清洗？", "a_zh": "可拆卸清洗，温水加温和洗涤剂；不锈钢食碗可进洗碗机，电子部件不可浸水。",
         "q_en": "How do I clean the feeder?",
         "a_en": "Detach and clean with warm water and mild detergent. Steel bowl is dishwasher safe; never immerse electronics."},
        {"q_zh": "如何选择狗项圈尺寸？", "a_zh": "测量颈围：S 25-30cm，M 30-38cm，L 38-50cm；介于两码之间选大码。",
         "q_en": "What collar size should I choose?",
         "a_en": "Measure neck: S 25-30cm, M 30-38cm, L 38-50cm. If between sizes, choose the larger."},
    ],
    "家居用品": [
        {"q_zh": "收纳箱承重多少？", "a_zh": "标准款单箱承重 15kg，叠放不超过 3 层。",
         "q_en": "What is the load capacity?",
         "a_en": "15kg per box, stack no more than 3 layers."},
        {"q_zh": "是否支持定制尺寸？", "a_zh": "支持起订量 500 件以上的定制，交期约 25 天。",
         "q_en": "Do you support custom sizes?",
         "a_en": "Yes, MOQ 500 units, lead time about 25 days."},
    ],
    "3C数码": [
        {"q_zh": "充电器支持哪些快充协议？", "a_zh": "支持 PD3.0/QC4+，65W 双向快充。",
         "q_en": "Which fast-charging protocols are supported?",
         "a_en": "PD3.0/QC4+, 65W bidirectional fast charging."},
        {"q_zh": "锂电池产品能空运吗？", "a_zh": "需符合 UN38.3 与航空运输包装指令，随附检测报告。",
         "q_en": "Can lithium battery products be shipped by air?",
         "a_en": "Yes with UN38.3 compliance and air transport packaging instructions."},
    ],
}

# 会话历史（M3 迁 Postgres；当前进程内，重启即丢）
_SESSIONS: dict[str, list[dict[str, str]]] = {}


def detect_emotion(message: str) -> dict[str, Any]:
    """关键词加权情绪识别；无命中为中性。"""
    text = (message or "").lower()
    scores = {k: sum(v["weight"] for w in v["words"] if w.lower() in text)
              for k, v in EMOTIONS.items()}
    if not any(scores.values()):
        return {"type": "neutral", "label": "中性", "score": 0}
    top = max(scores, key=lambda k: scores[k])
    return {"type": top, "label": EMOTIONS[top]["label"], "score": scores[top]}


def detect_dispute(message: str) -> dict[str, Any] | None:
    """命中纠纷关键词即返回类型与建议动作。"""
    text = (message or "").lower()
    for key, spec in DISPUTES.items():
        if any(w.lower() in text for w in spec["words"]):
            return {"type": key, "label": spec["label"],
                    "level": spec["level"], "action": spec["action"]}
    return None


def _ngrams(text: str, n: int = 2) -> set[str]:
    """字符 n-gram：中文没有空格分词，按空白切词会把整句当一个 token。

    "自动喂食器怎么清洗" 与 FAQ 的 "自动喂食器如何清洗" 用二元组重合
    （自动/动喂/喂食/食器/…/清洗）即可命中，无需引入分词依赖。
    """
    compact = re.sub(r"\s+", "", (text or "").lower())
    if len(compact) < n:
        return {compact} if compact else set()
    return {compact[i:i + n] for i in range(len(compact) - n + 1)}


def match_faq(message: str, category: str, language: str = "zh") -> list[dict[str, str]]:
    """FAQ 二元组重合度打分，返回 Top 3（问题/答案按语言取）。"""
    entries = FAQ.get(category, []) + FAQ.get("通用", [])
    msg_grams = _ngrams(message)
    scored: list[tuple[int, dict[str, str]]] = []
    for e in entries:
        q = e.get(f"q_{language}") or e.get("q_zh") or ""
        hits = len(msg_grams & _ngrams(q))
        if hits >= 2:                       # 至少两个二元组重合才算命中，防单字误配
            scored.append((hits, e))
    scored.sort(key=lambda x: -x[0])
    out = []
    for _, e in scored[:3]:
        out.append({"question": e.get(f"q_{language}") or e.get("q_zh", ""),
                    "answer": e.get(f"a_{language}") or e.get("a_zh", "")})
    return out


def _template_reply(message: str, emotion: dict[str, Any],
                    dispute: dict[str, Any] | None, faqs: list[dict[str, str]],
                    language: str) -> str:
    """模板回复：先安抚，再给事实（FAQ），纠纷给动作。"""
    if language == "en":
        head = {"angry": "We sincerely apologize for the experience.",
                "negative": "Thank you for your feedback.",
                "positive": "Thank you for your kind words!",
                "neutral": "Thanks for reaching out."}[emotion["type"]]
        body = f" {faqs[0]['answer']}" if faqs else ""
        tail = f" Next step: {dispute['action']}." if dispute else ""
        return head + body + tail
    head = {"angry": "非常抱歉给您带来不好的体验。",
            "negative": "感谢您的反馈。",
            "positive": "感谢您的认可！",
            "neutral": "您好，感谢咨询。"}[emotion["type"]]
    body = f" {faqs[0]['answer']}" if faqs else ""
    tail = f" 处理动作：{dispute['action']}。" if dispute else ""
    return head + body + tail


async def _llm_reply(message: str, emotion: dict[str, Any],
                     dispute: dict[str, Any] | None, faqs: list[dict[str, str]],
                     language: str) -> str | None:
    """LLM 组织回复：事实只准来自 FAQ，禁止编造政策/承诺。"""
    if not llm.available():
        return None
    facts = "\n".join(f"Q: {f['question']}\nA: {f['answer']}" for f in faqs) or "（无匹配 FAQ）"
    text = await llm.chat(
        "你是跨境电商客服。只准依据给出的 FAQ 事实回答，禁止编造政策、承诺赔偿金额或时效；"
        f"用{'英文' if language == 'en' else '中文'}回复，先回应用户情绪再给事实，不超过 120 字。",
        f"用户消息：{message}\n情绪：{emotion['label']}\n"
        f"纠纷：{dispute['label'] if dispute else '无'}\nFAQ 事实：\n{facts}",
        max_tokens=800,   # 推理模型（Atria-Dawn）会把预算先花在 reasoning 上，
                          # 300 会让 content 为空导致白白降级为模板
    )
    return text.strip() if text else None


async def chat(message: str, category: str = "通用", language: str = "zh",
               session_id: str = "default") -> dict[str, Any]:
    """单轮客服：情绪 → 纠纷 → FAQ → 回复（LLM 优先，模板兜底）。"""
    message = (message or "").strip()
    if not message:
        return {"success": False, "agent": AGENT, "message": "消息为空"}

    emotion = detect_emotion(message)
    dispute = detect_dispute(message)
    faqs = match_faq(message, category, language)
    reply = await _llm_reply(message, emotion, dispute, faqs, language)
    generated_by = "llm" if reply else "template"
    if reply is None:
        reply = _template_reply(message, emotion, dispute, faqs, language)

    history = _SESSIONS.setdefault(session_id, [])
    history.append({"role": "user", "content": message})
    history.append({"role": "assistant", "content": reply})
    del history[:-20]                      # 只保留最近 10 轮

    return {
        "success": True,
        "agent": AGENT,
        "data": {
            "reply": reply,
            "emotion": emotion,
            "dispute": dispute,
            "faqs": faqs,
            "category": category,
            "language": language,
            "generated_by": generated_by,
            "data_quality": "sample_faq",
            "note": "FAQ 为静态样本；LLM 回复仅组织语言，不代表平台官方政策",
            "session_id": session_id,
            "message_count": len(history),
        },
        "message": f"情绪={emotion['label']}"
                   + (f"｜纠纷={dispute['label']}" if dispute else "")
                   + f"｜FAQ 命中 {len(faqs)} 条",
    }
