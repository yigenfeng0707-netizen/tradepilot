# Copyright 2026 TradePilot project. Apache-2.0.
"""内容生成 Agent（M2）：多语言 × 多平台的上架文案包。

**这个模块产出的是文案，不是数据。** 因此 `data_quality="generated"`——
与 `real`（真实抓取）/`sample`（样本库）并列的第三类，不是从属关系。
把生成内容标成 real 会让"每个数字可溯源"的承诺失效。

三条硬规矩：

1. **语言是硬约束，不是提示词建议**。实测过：不指定目标语言时模型会用中文写
   美国站五点（标题英文、正文中文）。所以每次生成后都用脚本特征做校验
   （CJK / 泰文 / 谚文 / 阿拉伯文 / 假名），不合格带着原因重试一次，
   再不合格就退回模板并标注——绝不把语言错误的文案当成品返回。

2. **不编造产品参数**。产品名是用户输入（`user_input`），不是数据；尺寸、材质、
   承重等一律不写，需要卖家填的位置用 `[ADD: ...]` 占位符显式标出。
   有市场数据时只用它做**定位参考**（价格带、竞品标题用词），不当成本品规格。

3. **平台字数上限是配置常量，不是实时校验结果**。各平台后台规则会变，
   这里维护的是公开文档口径，输出里带 `rules_source` + `rules_verified: False`。
   合规校验的结论是"按配置上限校验"，不等于"平台一定放行"。

模板兜底只覆盖 6 种语言（zh/en/id/ms/vi/th，即东南亚常用语）。ja/ko/ar 走 LLM-only：
没有 LLM 时如实报错，不拿英文模板硬凑一个"日语版本"。
"""
from __future__ import annotations

import json
import re
from typing import Any

from .. import llm
from ..tools import competitor
from . import base
from .sample_market import CATEGORY_KEYWORDS, MARKET_SOURCES

AGENT = "内容生成Agent"

# ---- 语言表 ----------------------------------------------------------------
# script 用于脚本特征校验。id 与 ms 同为拉丁字母且词汇高度重叠，**不做区分**
# （做也做不准，硬做只会误报），这一点在 gaps 里明说。
LANGUAGES: dict[str, dict[str, str]] = {
    "zh": {"label": "简体中文", "english": "Simplified Chinese", "script": "cjk"},
    "en": {"label": "英语", "english": "English", "script": "latin"},
    "id": {"label": "印尼语", "english": "Indonesian", "script": "latin"},
    "ms": {"label": "马来语", "english": "Malay", "script": "latin"},
    "vi": {"label": "越南语", "english": "Vietnamese", "script": "latin"},
    "th": {"label": "泰语", "english": "Thai", "script": "thai"},
    "ja": {"label": "日语", "english": "Japanese", "script": "kana"},
    "ko": {"label": "韩语", "english": "Korean", "script": "hangul"},
    "ar": {"label": "阿拉伯语", "english": "Arabic", "script": "arabic"},
}

# ---- 平台规则（配置常量，非实时）------------------------------------------
# 数值取自平台公开文档的通用上限；分品类规则可能更严（如 Amazon 部分类目
# 标题上限 80）。所以校验结论一律表述为"按配置上限校验"。
_RULES_SOURCE = "平台公开文档通用上限（配置常量，未做实时校验；分品类可能更严）"

PLATFORMS: dict[str, dict[str, Any]] = {
    "amazon": {"label": "Amazon", "title_max": 200, "desc_max": 2000,
               "highlight_n": 5, "highlight_name": "五点描述"},
    "shopee": {"label": "Shopee", "title_max": 255, "desc_max": 3000,
               "highlight_n": 5, "highlight_name": "卖点"},
    "lazada": {"label": "Lazada", "title_max": 255, "desc_max": 3000,
               "highlight_n": 5, "highlight_name": "卖点"},
    "tiktok": {"label": "TikTok Shop", "title_max": 255, "desc_max": 2000,
               "highlight_n": 5, "highlight_name": "卖点"},
}

# ---- 脚本特征 --------------------------------------------------------------
_SCRIPTS: dict[str, re.Pattern[str]] = {
    "cjk": re.compile(r"[\u4e00-\u9fff]"),
    "thai": re.compile(r"[\u0e00-\u0e7f]"),
    "kana": re.compile(r"[\u3040-\u30ff]"),
    "hangul": re.compile(r"[\uac00-\ud7af\u1100-\u11ff]"),
    "arabic": re.compile(r"[\u0600-\u06ff]"),
}


def _language_issue(text: str, lang: str) -> str | None:
    """脚本特征校验：返回问题描述，合格返回 None。

    这是**必要条件检查**，不是充分条件：通过了只能说明"没有明显串语言"，
    不能保证语法地道。地道与否需要母语者复核——这一点写进 gaps。

    关键一条：非中文语种里出现任何汉字都判不合格。最常见的原因是**产品名原文
    被原样塞进译文**（"自动喂食器 — [ADD: ...]" 出现在泰语标题里）。这不是小瑕疵：
    买家看到的是一句泰语中间夹着四个汉字，等于没翻译。
    """
    spec = LANGUAGES.get(lang)
    if not spec:
        return f"未登记的语言代码 {lang}"
    if not (text or "").strip():
        return "内容为空"
    script = spec["script"]
    if script == "cjk":
        # 中文文案必须真有汉字；纯英文输出等于没翻译
        if not _SCRIPTS["cjk"].search(text):
            return "中文文案里没有汉字"
        return None
    hit_cjk = _SCRIPTS["cjk"].search(text)
    # 日语例外：汉字是日语的组成部分，不能用"有汉字"判未翻译。
    # 日语靠假名区分（见下方 script 分支）；其余非中文语种出现汉字即未翻译。
    if hit_cjk and script != "kana":
        return (f"{spec['label']}文案里出现中文「{hit_cjk.group(0)}」"
                "（产品名原文未翻译）")
    if script == "latin":
        for name in ("thai", "kana", "hangul", "arabic"):
            hit = _SCRIPTS[name].search(text)
            if hit:
                return f"{spec['label']}文案里出现{name}字符「{hit.group(0)}」"
        return None
    if not _SCRIPTS[script].search(text):
        return f"{spec['label']}文案里没有{script}字符，疑似未翻译"
    return None


def _has_cjk(text: str) -> bool:
    return bool(_SCRIPTS["cjk"].search(text or ""))


# ---- 模板兜底（6 语言）-----------------------------------------------------
# 只写不需要产品参数就能成立的句子；参数一律 [ADD] 占位，绝不编造。
_TEMPLATES: dict[str, dict[str, Any]] = {
    "zh": {
        "title": "{kw} —— [ADD: 核心属性，如尺寸或材质]",
        "bullets": [
            "日常可用：[ADD: 主要材质与结构，加一项可验证参数（厚度/承重/面料成分）]",
            "好打理：[ADD: 清洁方式——机洗、局部擦拭或可拆洗，以及建议频次]",
            "尺寸要对：[ADD: 具体尺寸与适用范围，提醒买家下单前测量]",
            "用在哪：[ADD: 适用房间、地面类型或车内场景，说明防滑表现]",
            "包装清单：[ADD: 件数、外套、内芯及随附卡片]",
        ],
        "description": (
            "{kw}。本稿在缺少产品规格的情况下生成，规格字段以 [ADD] 标出，"
            "需卖家补齐后再上架；市场价格参考与合规要点来自任务报告，不属于面向买家的文案。"
        ),
        "cta": "立即查看",
        "hashtags": ["#{tag}"],
    },
    "en": {
        "title": "{kw} — [ADD: core attribute such as size or material]",
        "bullets": [
            "Built for daily use: [ADD: primary material and construction, plus one "
            "verifiable spec such as thickness, load capacity, or fabric composition]",
            "Easy care routine: [ADD: how to clean it and how often buyers should do it]",
            "Right size matters: [ADD: dimensions and the size range this suits]",
            "Where it fits: [ADD: rooms, floor types, crate or car use]",
            "What's in the box: [ADD: item count, cover, insert, and any care card]",
        ],
        "description": (
            "{kw}. This draft was generated without product specifications, so "
            "specification fields are marked [ADD] for the seller to complete."
        ),
        "cta": "Shop now",
        "hashtags": ["#{tag}"],
    },
    "id": {
        "title": "{kw} — [ADD: atribut utama seperti ukuran atau bahan]",
        "bullets": [
            "Untuk pemakaian harian: [ADD: bahan utama dan konstruksi, plus satu "
            "spesifikasi yang bisa diverifikasi]",
            "Mudah dirawat: [ADD: cara membersihkan dan seberapa sering]",
            "Ukuran penting: [ADD: dimensi dan rentang ukuran yang cocok]",
            "Cocok dipakai di: [ADD: ruangan, jenis lantai, atau di dalam mobil]",
            "Isi paket: [ADD: jumlah item, sarung, isi, dan kartu perawatan]",
        ],
        "description": (
            "{kw}. Draf ini dibuat tanpa spesifikasi produk, sehingga kolom "
            "spesifikasi ditandai [ADD] untuk dilengkapi penjual."
        ),
        "cta": "Beli sekarang",
        "hashtags": ["#{tag}"],
    },
    "ms": {
        "title": "{kw} — [ADD: atribut utama seperti saiz atau bahan]",
        "bullets": [
            "Untuk kegunaan harian: [ADD: bahan utama dan pembinaan, serta satu "
            "spesifikasi yang boleh disahkan]",
            "Mudah dijaga: [ADD: cara membersihkan dan kekerapan disyorkan]",
            "Saiz penting: [ADD: dimensi dan julat saiz yang sesuai]",
            "Sesuai digunakan di: [ADD: bilik, jenis lantai, atau dalam kereta]",
            "Kandungan kotak: [ADD: bilangan item, sarung, isi, dan kad penjagaan]",
        ],
        "description": (
            "{kw}. Draf ini dijana tanpa spesifikasi produk, jadi ruangan "
            "spesifikasi ditanda [ADD] untuk dilengkapkan oleh penjual."
        ),
        "cta": "Beli sekarang",
        "hashtags": ["#{tag}"],
    },
    "vi": {
        "title": "{kw} — [ADD: thuộc tính chính như kích thước hoặc chất liệu]",
        "bullets": [
            "Dùng hằng ngày: [ADD: chất liệu chính và kết cấu, kèm một thông số "
            "có thể kiểm chứng]",
            "Dễ vệ sinh: [ADD: cách làm sạch và tần suất khuyến nghị]",
            "Kích thước quan trọng: [ADD: kích thước và khoảng phù hợp]",
            "Phù hợp đặt ở: [ADD: phòng, loại sàn, hoặc trong xe]",
            "Trong hộp có: [ADD: số món, vỏ, ruột và thẻ hướng dẫn]",
        ],
        "description": (
            "{kw}. Bản nháp này được tạo khi chưa có thông số sản phẩm, nên các "
            "trường thông số được đánh dấu [ADD] để người bán bổ sung."
        ),
        "cta": "Mua ngay",
        "hashtags": ["#{tag}"],
    },
    "th": {
        "title": "{kw} — [ADD: คุณสมบัติหลัก เช่น ขนาดหรือวัสดุ]",
        "bullets": [
            "ใช้ได้ทุกวัน: [ADD: วัสดุหลักและโครงสร้าง พร้อมสเปกที่ตรวจสอบได้หนึ่งรายการ]",
            "ดูแลง่าย: [ADD: วิธีทำความสะอาดและความถี่ที่แนะนำ]",
            "ขนาดสำคัญ: [ADD: ขนาดและช่วงที่เหมาะสม]",
            "วางที่ไหนได้บ้าง: [ADD: ห้อง ประเภทพื้น หรือในรถ]",
            "ในกล่องมีอะไร: [ADD: จำนวนชิ้น ปลอก ไส้ และการ์ดแนะนำ]",
        ],
        "description": (
            "{kw} ฉบับร่างนี้สร้างขึ้นโดยไม่มีสเปกสินค้า ช่องสเปกจึงถูกทำเครื่องหมาย "
            "[ADD] ให้ผู้ขายเติมเอง"
        ),
        "cta": "ช้อปเลย",
        "hashtags": ["#{tag}"],
    },
}

# 各语言的默认 CTA：LLM 漏填时用它兜底，比塞一个 [ADD] 占位符更合理
_CTA_FALLBACK = {
    "zh": "立即查看", "en": "Shop now", "id": "Beli sekarang",
    "ms": "Beli sekarang", "vi": "Mua ngay", "th": "ช้อปเลย",
    "ja": "今すぐ見る", "ko": "지금 보기", "ar": "تسوق الآن",
}

# 有模板兜底的语言；其余语言必须走 LLM，否则如实报错
_TEMPLATE_LANGS = frozenset(_TEMPLATES)


def _dedupe_words(text: str) -> str:
    seen: set[str] = set()
    out = []
    for w in (text or "").split():
        if w.lower() not in seen:
            seen.add(w.lower())
            out.append(w)
    return " ".join(out)


def _title_without_placeholders(title: str) -> tuple[str, bool]:
    """把标题裁到第一个 `[ADD]` 之前，返回 (新标题, 是否裁过)。

    标题是**发布面**：带 `[ADD: capacity]` 的标题在 Amazon 后台过不了审核，
    买家看到的也是一句夹着方括号的残句。

    为什么是"裁断"而不是"删掉占位符"：模型写的是
    `[ADD: material] BPA-Free Construction`，直接删会得到
    `BPA-Free Construction`——凭空多出一条卖家没提供的材质声明。
    裁断只保留模型已经写实的部分，不会制造新的事实。
    """
    if "[ADD" not in (title or ""):
        return title, False
    head = re.split(r"\s*\[ADD", title, maxsplit=1)[0]
    head = head.strip().strip(" ,，;；、—-–|/+&")
    # 裁完太短说明占位符就在开头（如模板骨架），留着反而更能提示卖家
    if len(head) < 3:
        return title, False
    return head, True


def _derive_hashtags(title: str, keywords: list[Any], limit: int = 3) -> list[str]:
    """模型没给话题标签时，用 SEO 关键词或标题片段拼出来。

    不能只取标题第一个词：实测得到 `#Automatic`——一个没人会搜的标签。
    """
    out: list[str] = []
    seen: set[str] = set()
    sources = [str(k) for k in keywords] + [title or ""]
    for src in sources:
        if not src or "[ADD" in src or "[" in src:
            continue
        for chunk in re.split(r"[,，、;；|/]", src):
            words = [w for w in chunk.split() if w.strip(".,!?")]
            if not words:
                continue
            # 中文标签不拼词，直接取第一个词；拼起来会变成一长串没人搜的组合
            if any(_has_cjk(w) for w in words):
                tag = words[0].strip(".,!?")
            else:
                tag = "".join(w[:1].upper() + w[1:] for w in words[:3])
            if len(tag) < 3 or tag.lower() in seen:
                continue
            seen.add(tag.lower())
            out.append(f"#{tag}")
            if len(out) >= limit:
                return out
    return out


def _scan_value(s: str, j: int) -> int | None:
    """从 s[j] 起扫一个 JSON 值，返回值结束后的下标；扫不完返回 None。"""
    n = len(s)
    if j >= n:
        return None
    ch = s[j]
    if ch == '"':
        k = j + 1
        while k < n:
            if s[k] == "\\":
                k += 2
                continue
            if s[k] == '"':
                return k + 1
            k += 1
        return None
    if ch in "{[":
        depth = 0
        k = j
        while k < n:
            c = s[k]
            if c == '"':
                k += 1
                while k < n:
                    if s[k] == "\\":
                        k += 2
                        continue
                    if s[k] == '"':
                        break
                    k += 1
                if k >= n:
                    return None
                k += 1
                continue
            if c in "{[":
                depth += 1
            elif c in "}]":
                depth -= 1
                if depth == 0:
                    return k + 1
            k += 1
        return None
    k = j
    while k < n and s[k] not in ",}]":
        k += 1
    return k if k > j else None


def _salvage_json(raw: str) -> dict[str, Any] | None:
    """从被截断的 JSON 里抢救出**已经完整**的顶层字段。

    实测：reasoner 模型把 max_tokens 大量花在推理上，输出常在 social_copy
    中途断掉，`_extract_json` 直接返回 None，于是整包丢弃、退回模板骨架——
    卖家最需要的标题/描述/卖点其实早就写好了。

    只做保守恢复：逐键扫描，遇到第一个读不完整的值就停，绝不补内容——
    补出来的字段可能是编的，比缺失更糟。
    """
    if not raw:
        return None
    start = raw.find("{")
    if start < 0:
        return None
    s = raw[start:]
    n = len(s)
    i = 1                       # s[0] 是 '{'
    out: dict[str, Any] = {}
    while i < n:
        while i < n and s[i] in " \t\r\n,":
            i += 1
        if i >= n or s[i] != '"':
            break
        key_end = i + 1
        while key_end < n:
            if s[key_end] == "\\":
                key_end += 2
                continue
            if s[key_end] == '"':
                break
            key_end += 1
        if key_end >= n:
            break
        key_raw = s[i:key_end + 1]
        j = key_end + 1
        while j < n and s[j] in " \t\r\n":
            j += 1
        if j >= n or s[j] != ":":
            break
        j += 1
        while j < n and s[j] in " \t\r\n":
            j += 1
        v_end = _scan_value(s, j)
        if v_end is None:
            break
        try:
            out[json.loads(key_raw)] = json.loads(s[j:v_end])
        except (ValueError, TypeError):
            break
        i = v_end
    return out or None


def _template_pack(product_name: str, lang: str) -> dict[str, Any]:
    tpl = _TEMPLATES[lang]
    kw = _dedupe_words(product_name).strip() or product_name
    # 模板不会翻译产品名。非中文语种里直接嵌中文原文 = 一句泰语夹四个汉字，
    # 那不是"待补规格"而是"没翻译"。所以换成显式占位符，让卖家填当地语言名称。
    if lang != "zh" and _has_cjk(kw):
        kw = f"[ADD: product name in {LANGUAGES[lang]['english']}]"
    return {
        "title": tpl["title"].format(kw=kw),
        "description": tpl["description"].format(kw=kw),
        "highlights": [{"icon": "✓", "text": b} for b in tpl["bullets"]],
        "seo_keywords": [kw],
        "marketing": {
            "social_copy": {
                "hook": tpl["title"].format(kw=kw),
                "pain_point": "[ADD: 买家最常抱怨的一个具体问题]"
                              if lang == "zh" else "[ADD: the one complaint buyers repeat]",
                "solution": "[ADD: 本品如何解决它，不写未提供的参数]"
                            if lang == "zh" else "[ADD: how this product solves it, no specs]",
                "cta": tpl["cta"],
                "hashtags": [tpl["hashtags"][0].format(tag=kw.replace(" ", ""))],
            },
            "ad_copy": {
                "headline": tpl["title"].format(kw=kw),
                "body": tpl["description"].format(kw=kw),
                "cta_button": tpl["cta"],
            },
        },
    }


def _facts_block(market_facts: dict[str, Any] | None) -> dict[str, Any]:
    """喂给 LLM 的事实块。只放真实抓取到的东西，且标明是竞品口径。"""
    if not market_facts:
        return {}
    band = market_facts.get("price_band") or {}
    return {
        "competitor_price_band": {
            "min": band.get("min"), "median": band.get("median"),
            "max": band.get("max"), "currency": market_facts.get("currency"),
        },
        "competitor_sample_titles": (market_facts.get("titles") or [])[:3],
        "note": "价格带与标题是**竞品**数据，只能用于定位参考，不是本品规格",
    }


async def _llm_pack(product_name: str, category: str, platform: str, lang: str,
                    rules: dict[str, Any], market_facts: dict[str, Any] | None,
                    language_note: str | None = None,
                    retry_on_failure: bool = False,
                    ) -> tuple[dict[str, Any] | None, str | None]:
    """LLM 生成整包文案，返回 (pack, 失败原因)。

    **默认只调用一次**。原因是实测的延迟约束：本网关的 reasoner 模型单次生成
    约 90 秒（推理也消耗 max_tokens），两次尝试叠加就超过 200 秒，接口会被
    客户端或网关掐断——比"退回模板"更糟。需要重试的调用方显式传
    `retry_on_failure=True`，并自行承担 3 分钟级延迟。

    失败原因分两类，都原样返回给调用方，不静默降级：
    - 解析失败 → 几乎都是**输出被截断**（泰语等 token 密度高的语种尤甚，
      JSON 被截在字符串中间，`_extract_json` 返回 None）；
    - 语言不合格 → 带着具体问题（哪个字、哪个语种）描述。
    """
    spec = LANGUAGES[lang]
    facts = _facts_block(market_facts)

    async def attempt(note: str | None, budget: int) -> dict[str, Any] | None:
        retry = f"\n上次不合格：{note}。必须修正。" if note else ""
        # 全链路会把选品关键词（英文）直接喂进来，此时不能再说"产品名是中文"。
        name_rule = (f"产品名是中文，必须翻译成{spec['label']}，不得保留中文原文。\n"
                     if _has_cjk(product_name) else
                     f"产品名已是英文，写{spec['label']}时翻译它；"
                     f"若目标语言就是英语则原样使用。\n")
        # 提示词刻意精简：这个网关是 reasoner 模型，max_tokens 同时装推理与正文，
        # 约束写得越多推理越长、正文越容易被截断（泰语等 token 密度高的语种尤甚）。
        system = (
            f"写{rules['label']}跨境上架文案，**全文只用{spec['label']}**"
            f"（{spec['english']}），不得混入中文或其他文字。\n"
            + name_rule +
            f"标题≤{rules['title_max']}字符；描述150–300字符；"
            f"卖点{rules['highlight_n']}条、每条≤60字符；全部精炼。\n"
            "不编造参数（尺寸/材质/承重/认证），未知规格写 [ADD: 用目标语言说明]，"
            "但**标题里不能出现 [ADD]**（标题要能直接上架），未知规格干脆不写。\n"
            "不写促销语（best seller/#1/free shipping）、不声称销量排名、"
            "竞品价格只用于定位不写成本品价格。\n"
            '只输出JSON，无其他文字：{"title":"","description":"","highlights":[""],'
            '"seo_keywords":[""],"social_copy":{"hook":"","pain_point":"","solution":"",'
            '"cta":"","hashtags":[""]},"ad_copy":{"headline":"","body":"","cta_button":""}}'
            + retry
        )
        user = (f"产品名：{product_name}\n品类：{category}\n"
                f"平台：{rules['label']}\n语言：{spec['label']}\n"
                f"真实市场事实（竞品口径）："
                f"{json.dumps(facts, ensure_ascii=False) if facts else '无（未取到真实数据）'}")
        raw = await llm.chat(system, user, max_tokens=budget)
        if raw is None:
            return None
        parsed = llm._extract_json(raw)
        salvaged = False
        if not parsed:
            # 截断时别整包丢弃：前面写好的字段照样能用
            parsed = _salvage_json(raw)
            salvaged = bool(parsed)
        if not parsed or not parsed.get("title"):
            return None
        if salvaged:
            parsed["_salvaged"] = True
        # 结构归一：卖点截断到平台条数，避免下游按数量渲染时崩
        hl = [str(h) for h in (parsed.get("highlights") or []) if str(h).strip()]
        parsed["highlights"] = hl[: rules["highlight_n"]]
        parsed.setdefault("seo_keywords", [])
        # 提示词要的是顶层 social_copy / ad_copy，下游读的是 marketing.*。
        # 这两处曾经对不上：模型写得再好，社媒/广告整块都会被当成"未返回"
        # 而全部走兜底，等于白生成一半内容。两种形状都收，避免再次漂移。
        mk = parsed.get("marketing")
        if not isinstance(mk, dict) or not mk:
            mk = {k: parsed[k] for k in ("social_copy", "ad_copy")
                  if isinstance(parsed.get(k), dict)}
        parsed["marketing"] = mk
        return parsed

    # 预算刻意压得不高：reasoner 模型会把 max_tokens 大量消耗在推理上。
    # 配合"描述 250–450 字符"的硬要求，2200 足够完整输出一个 JSON。
    budgets = (2200, 2600) if retry_on_failure else (2200,)
    note = language_note
    for budget in budgets:
        parsed = await attempt(note, budget)
        if parsed is None:
            note = ("输出被截断或 JSON 不完整，请把描述压到 250 字符内、"
                    "卖点每条 60 字符内，并确保输出是完整可解析的 JSON")
            continue
        issue = _language_issue(
            f"{parsed.get('title', '')} {parsed.get('description', '')}", lang)
        if issue:
            note = issue
            continue
        return parsed, None
    return None, note or "模型未返回可解析内容"


def _check(title: str, desc: str, rules: dict[str, Any], lang: str) -> dict[str, Any]:
    """平台规则校验 + 语言校验。结论一律带"按配置上限"的限定语。"""
    warnings: list[str] = []
    title_len, desc_len = len(title or ""), len(desc or "")
    title_ok = title_len <= rules["title_max"]
    desc_ok = desc_len <= rules["desc_max"]
    if not title_ok:
        warnings.append(
            f"标题 {title_len} 字符，超过 {rules['label']} 配置上限 "
            f"{rules['title_max']} 字符（分品类可能更严）")
    if not desc_ok:
        warnings.append(
            f"描述 {desc_len} 字符，超过 {rules['label']} 配置上限 "
            f"{rules['desc_max']} 字符")
    lang_issue = _language_issue(f"{title} {desc}", lang)
    if lang_issue:
        warnings.append(f"语言校验未通过：{lang_issue}")
    if "[ADD" in (title + desc):
        warnings.append("文案含 [ADD] 占位符：规格字段需卖家补齐后才能上架")
    return {"title_length_ok": title_ok, "desc_length_ok": desc_ok,
            "title_len": title_len, "title_max": rules["title_max"],
            "desc_len": desc_len, "desc_max": rules["desc_max"],
            "language_ok": lang_issue is None, "warnings": warnings,
            "rules_source": _RULES_SOURCE, "rules_verified": False}


async def _market_facts(category: str, marketplace: str, keyword: str,
                        product_name: str) -> dict[str, Any] | None:
    """可选的真实市场数据：抓不到就返回 None，文案照常生成但会标注无市场依据。

    检索词优先级：显式 keyword > 产品名 > 品类默认词。
    **不能只用品类默认词**：品类「宠物用品」的默认词是 "dog bed"，若产品其实是
    「自动喂食器」，拿回来的竞品全是狗床，价格带与用词定位就完全跑偏了
    （实测踩过：喂食器的"竞品价格带"中位数 516 元，那是狗床的价格）。
    """
    sources = MARKET_SOURCES.get(marketplace, ())
    if not sources:
        return None
    cat_word, cat_word_cn = CATEGORY_KEYWORDS.get(category, ("", ""))
    term = (keyword or product_name or cat_word).strip()
    if not term:
        return None
    # 中文词要连中文别名一起给，dangdang 这类国内源对中文检索更稳
    term_cn = term if re.search(r"[\u4e00-\u9fff]", term) else ""
    for source in sources:
        outcome = await competitor.run({
            "keyword": term, "keyword_cn": term_cn or cat_word_cn,
            "category": category, "source": source, "limit": 8,
        })
        if outcome.ok and outcome.data:
            d = outcome.data
            return {
                "source": source, "currency": d.get("currency"),
                "keyword_used": d.get("keyword_used"),
                "price_band": d.get("price_band"),
                "titles": [i["title"] for i in (d.get("items") or [])],
                "provenance_ids": [i["provenance_id"] for i in
                                   (d.get("items") or []) if i.get("provenance_id")],
            }
    return None


async def run(product_name: str, category: str = "", platform: str = "shopee",
              target_language: str = "en", marketplace: str = "US",
              keyword: str = "", use_market_data: bool = True,
              retry_on_failure: bool = False) -> dict[str, Any]:
    """生成上架文案包：标题 / 描述 / 卖点 / SEO 词 / 社媒 / 广告。

    `retry_on_failure` 默认关：单次 LLM 生成约 90 秒，重试会把接口拖到 3 分钟级。
    需要更高成功率的批量调用方可以打开，交互界面保持关闭。
    """
    product_name = (product_name or "").strip()
    if not product_name:
        return base.fail(AGENT, "缺少 product_name（产品名）")

    platform = (platform or "").strip().lower()
    rules = PLATFORMS.get(platform)
    if not rules:
        return base.fail(
            AGENT, f"未支持的平台 {platform}",
            {"supported": sorted(PLATFORMS)})

    lang = (target_language or "en").strip().lower()
    if lang not in LANGUAGES:
        return base.fail(AGENT, f"未支持的语言 {lang}",
                         {"supported": sorted(LANGUAGES)})

    market_facts = (await _market_facts(category, marketplace, keyword, product_name)
                    if use_market_data else None)

    generated_by = "template"
    pack: dict[str, Any] | None = None
    lang_note: str | None = None

    if llm.available():
        # _llm_pack 内部已做尝试（默认一次，retry_on_failure 时才两次），
        # 这里不再重复调用——每次调用几十秒，重复调用会把接口拖到 4 分钟。
        pack, lang_note = await _llm_pack(
            product_name, category or "未指定", platform, lang, rules, market_facts,
            retry_on_failure=retry_on_failure)
        if pack:
            generated_by = "llm（基于卖家输入 + 竞品定位参考）"
        else:
            pack = None
    from_llm = pack is not None

    if pack is None:
        if lang not in _TEMPLATE_LANGS:
            return base.fail(
                AGENT,
                f"{LANGUAGES[lang]['label']}没有模板兜底，且 LLM "
                + ("输出未通过语言校验" if llm.available() else "不可用")
                + "。不拿其他语言的模板硬凑——那样交付的是一份假翻译。"
                "请配置 LLM 后重试，或改用有模板的语言"
                f"（{'/'.join(sorted(_TEMPLATE_LANGS))}）。",
                {"language": lang, "platform": platform})
        pack = _template_pack(product_name, lang)
        generated_by = ("template（合规骨架；[ADD] 处需卖家补齐规格）"
                        + (f"；LLM 未返回合格内容：{lang_note}" if lang_note else ""))

    title = str(pack.get("title") or "").strip()
    desc = str(pack.get("description") or "").strip()
    # 标题必须可直接上架：占位符一律裁掉（仅 LLM 产物；模板骨架本就靠 [ADD] 提示卖家）
    title_cut = False
    if from_llm:
        title, title_cut = _title_without_placeholders(title)
    compliance = _check(title, desc, rules, lang)
    if title_cut:
        compliance["warnings"].append(
            "标题中的规格占位符已裁掉（标题要能直接上架）——"
            "被裁的规格请补进描述与卖点，不要放回标题")
    if from_llm and pack.pop("_salvaged", False):
        compliance["warnings"].append(
            "模型输出被截断，已保留截断前完整的字段（标题/描述/卖点通常完好，"
            "社媒与广告可能缺失并走兜底）——需要完整版可重试一次")

    untranslated_name = (generated_by.startswith("template") and lang != "zh"
                         and _has_cjk(product_name))
    if untranslated_name:
        compliance["warnings"].append(
            f"产品名未翻译：「{product_name}」是中文原文，模板兜底不做翻译——"
            f"标题中已替换为 [ADD] 占位符，需填入{LANGUAGES[lang]['label']}名称")

    marketing = pack.get("marketing") or {}
    social = marketing.get("social_copy") or {}
    ad = marketing.get("ad_copy") or {}
    missing_marketing: list[str] = []
    if from_llm:
        # LLM 已产出整包时**不再混入模板占位符**：那会让结果一半是真文案、
        # 一半是 [ADD: product name in English]，看起来像坏了。
        # 缺的字段用标题/默认 CTA 兜底，并明确告知缺了什么。
        #
        # 注意：hashtags 必须在重建 social 之前取走。之前先重建再读
        # `social.get("hashtags")`，读到的是新字典里不存在的键，
        # 模型给的标签被整包丢掉、只剩兜底的 "#Automatic"。
        # 模型给的标签不一定带 #（泰语/日语常见），归一后再用，否则前端
        # 会渲染出一串没有井号的裸词。
        raw_hashtags = []
        for h in (social.get("hashtags") or []):
            tag = str(h).strip().lstrip("#").strip()
            if tag:
                raw_hashtags.append(f"#{tag}")
        social = {k: str(social.get(k) or "").strip()
                  for k in ("hook", "pain_point", "solution", "cta")}
        social["hashtags"] = raw_hashtags
        ad = {k: str(ad.get(k) or "").strip()
              for k in ("headline", "body", "cta_button")}
        cta = _CTA_FALLBACK.get(lang, "Shop now")
        if not social["hook"]:
            social["hook"] = title
            missing_marketing.append("社媒钩子")
        if not social["cta"]:
            social["cta"] = cta
            missing_marketing.append("社媒 CTA")
        if not social["hashtags"]:
            social["hashtags"] = _derive_hashtags(
                title, list(pack.get("seo_keywords") or []))
            if social["hashtags"]:
                missing_marketing.append("话题标签")
        if not ad["headline"]:
            ad["headline"] = title
            missing_marketing.append("广告标题")
        if not ad["body"]:
            ad["body"] = desc
            missing_marketing.append("广告正文")
        if not ad["cta_button"]:
            ad["cta_button"] = cta
            missing_marketing.append("广告按钮")
        if missing_marketing:
            compliance["warnings"].append(
                "社媒/广告文案部分字段模型未返回，已用标题/关键词/默认 CTA 兜底："
                + "、".join(missing_marketing))
    else:
        template = _template_pack(product_name, lang)["marketing"]
        social = {**template["social_copy"], **{k: v for k, v in social.items() if v}}
        ad = {**template["ad_copy"], **{k: v for k, v in ad.items() if v}}

    highlights = pack.get("highlights") or []
    if highlights and isinstance(highlights[0], str):
        highlights = [{"icon": "✓", "text": h} for h in highlights]

    # 社媒/广告文案不阻断交付，但语言不对必须说出来——它们是会给买家看的。
    mk_issue = _language_issue(
        json.dumps(social, ensure_ascii=False) + json.dumps(ad, ensure_ascii=False),
        lang)
    if mk_issue:
        compliance["warnings"].append(f"社媒/广告文案语言校验未通过：{mk_issue}")

    gaps = [
        "产品规格（尺寸/材质/承重/容量）：卖家未提供，文案中以 [ADD] 标出，不编造",
        "销量、排名、搜索量：无数据源，文案中不出现任何此类声称",
        f"平台字数上限为配置常量（{rules['label']}：标题 {rules['title_max']} / "
        f"描述 {rules['desc_max']}），未做实时校验，分品类可能更严",
        "语言仅做脚本特征校验（无串语言字符），语法地道性需母语者复核",
    ]
    if lang in ("id", "ms"):
        gaps.append("印尼语与马来语同为拉丁字母且词汇高度重叠，未做区分校验")
    if untranslated_name:
        gaps.append("产品名未翻译：模板兜底只做语言骨架，产品名需填入目标语言名称")
    if not market_facts:
        gaps.append("未取到真实竞品数据：价格带与用词定位缺依据（文案仍可生成）")

    data = {
        "product_name": product_name,
        "category": category or "未指定",
        "platform": platform,
        "platform_label": rules["label"],
        "target_language": lang,
        "language_label": LANGUAGES[lang]["label"],
        "content": {
            "title": title,
            "description": desc,
            "highlights": highlights,
            "seo_keywords": [str(k) for k in (pack.get("seo_keywords") or [])][:12],
        },
        "marketing": {"social_copy": social, "ad_copy": ad},
        "platform_compliance": compliance,
        "data_quality": base.QUALITY_GENERATED,
        "generated_by": generated_by,
        "llm_attempted": llm.available(),
        # LLM 调了但没用上：原因必须给出来，否则用户分不清"模板"是配置问题还是失败
        "llm_failure_reason": (lang_note if (llm.available()
                                             and generated_by.startswith("template"))
                               else None),
        "latency_note": ("LLM 单次生成约 90 秒；默认不重试，失败即退回模板并给出原因"),
        "market_facts": ({"source": market_facts["source"],
                          "keyword_used": market_facts.get("keyword_used"),
                          "price_band": market_facts["price_band"],
                          "currency": market_facts["currency"],
                          "competitor_n": len(market_facts["titles"]),
                          "provenance_ids": market_facts["provenance_ids"]}
                         if market_facts else None),
        "provenance_ids": market_facts["provenance_ids"] if market_facts else [],
        "gaps": gaps,
        "note": ("文案为生成内容（data_quality=generated），不是可溯源数据；"
                 "其中 [ADD] 占位符必须由卖家补齐真实规格后才能上架。"
                 "SEO 关键词是表达建议，不含搜索量依据。"),
    }
    return base.wrap(
        AGENT, data, base.QUALITY_GENERATED,
        f"{rules['label']} · {LANGUAGES[lang]['label']} 文案已生成（{generated_by}）；"
        f"标题 {compliance['title_len']}/{rules['title_max']}、"
        f"描述 {compliance['desc_len']}/{rules['desc_max']}；"
        f"市场依据：{'真实竞品 ' + str(len(market_facts['titles'])) + ' 条' if market_facts else '无'}")
