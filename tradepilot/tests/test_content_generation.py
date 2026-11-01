# -*- coding: utf-8 -*-
"""内容生成 Agent 的回归测试（M2）。

守的红线：
1. **语言是硬约束**：英文站不许出中文，日语没 LLM 时不许拿英文模板硬凑；
2. **不编造产品参数**：模板与 LLM 路径都必须用 [ADD] 占位，不写具体尺寸/材质；
3. **平台上限是配置常量**：结论必须带"按配置上限"的限定，且 rules_verified=False；
4. **产出是文案不是数据**：data_quality 恒为 generated，不冒充 real/sample。
"""
import unittest
from unittest import mock

from app.agents import content_generation as cg
from app.tools.executor import ToolOutcome


def _market_outcome(titles=None, currency="CNY"):
    titles = titles or ["狗床 大型犬 可拆洗", "宠物床 四季通用", "狗窝 夏季凉垫"]
    return ToolOutcome(True, "done", None, "扫描完成", {
        "keyword_used": "狗床", "source": "dangdang", "currency": currency,
        "scan_provenance_id": 900,
        "items": [{"title": t, "price": 100.0 + i * 20, "currency": currency,
                   "source": "dangdang", "source_url": f"https://x/{i}",
                   "provenance_id": 500 + i} for i, t in enumerate(titles)],
        "price_band": {"min": 100.0, "median": 120.0, "max": 140.0},
    })


class TestInputValidation(unittest.IsolatedAsyncioTestCase):
    async def test_missing_product_name(self):
        r = await cg.run("  ")
        self.assertFalse(r["success"])

    async def test_unsupported_platform(self):
        r = await cg.run("dog bed", platform="temu")
        self.assertFalse(r["success"])
        self.assertIn("未支持的平台", r["message"])

    async def test_unsupported_language(self):
        r = await cg.run("dog bed", target_language="fr")
        self.assertFalse(r["success"])
        self.assertIn("未支持的语言", r["message"])


class TestTemplateFallback(unittest.IsolatedAsyncioTestCase):
    """无 LLM 时的合规骨架：结构完整、语言正确、参数全部占位。"""

    async def _run(self, **kw):
        with mock.patch.object(cg.llm, "available", return_value=False), \
             mock.patch.object(cg.competitor, "run",
                               mock.AsyncMock(return_value=_market_outcome())):
            return await cg.run("dog bed", **kw)

    async def test_chinese_template(self):
        r = await self._run(target_language="zh")
        self.assertTrue(r["success"])
        d = r["data"]
        self.assertEqual(d["data_quality"], "generated")
        self.assertEqual(len(d["content"]["highlights"]), 5)
        # 中文模板必须真的是中文
        self.assertIsNone(cg._language_issue(d["content"]["title"], "zh"))
        self.assertIn("ADD", d["content"]["title"])

    async def test_english_template_has_no_cjk(self):
        r = await self._run(target_language="en")
        d = r["data"]
        blob = d["content"]["title"] + d["content"]["description"]
        self.assertIsNone(cg._language_issue(blob, "en"))

    async def test_sea_templates_are_in_target_language(self):
        for lang, marker in (("id", "ADD"), ("ms", "ADD"), ("vi", "ADD"),
                             ("th", "\u0e00")):
            r = await self._run(target_language=lang)
            self.assertTrue(r["success"], lang)
            blob = r["data"]["content"]["title"] + r["data"]["content"]["description"]
            if marker == "\u0e00":
                self.assertRegex(blob, r"[\u0e00-\u0e7f]", lang)
            self.assertIsNone(cg._language_issue(blob, lang), lang)

    async def test_no_fabricated_specs(self):
        """红线：模板卖点里不得出现具体尺寸/材质/承重数字。"""
        r = await self._run(target_language="zh")
        for h in r["data"]["content"]["highlights"]:
            self.assertIn("ADD", h["text"])

    async def test_japanese_without_llm_fails_honestly(self):
        """没有日语模板时不许拿英文硬凑——那是一份假翻译。"""
        r = await self._run(target_language="ja")
        self.assertFalse(r["success"])
        self.assertIn("没有模板兜底", r["message"])

    async def test_generated_by_mentions_template(self):
        r = await self._run(target_language="en")
        self.assertIn("template", r["data"]["generated_by"])

    async def test_chinese_product_name_replaced_in_thai_template(self):
        """中文产品名不能原样嵌进泰语句子：应换成 [ADD] 占位并给出警告。"""
        with mock.patch.object(cg.llm, "available", return_value=False), \
             mock.patch.object(cg.competitor, "run",
                               mock.AsyncMock(return_value=_market_outcome())):
            r = await cg.run("自动喂食器", category="宠物用品",
                             target_language="th")
        self.assertTrue(r["success"])
        d = r["data"]
        blob = d["content"]["title"] + d["content"]["description"]
        self.assertIsNone(cg._language_issue(blob, "th"))
        self.assertNotIn("自动喂食器", blob)
        self.assertIn("[ADD", d["content"]["title"])
        self.assertTrue(any("未翻译" in w for w in d["platform_compliance"]["warnings"]))
        self.assertTrue(any("产品名未翻译" in g for g in d["gaps"]))


class TestPlatformRules(unittest.TestCase):
    def test_rules_are_flagged_unverified(self):
        r = cg._check("t", "d", cg.PLATFORMS["amazon"], "en")
        self.assertFalse(r["rules_verified"])
        self.assertIn("配置常量", r["rules_source"])

    def test_over_long_title_flagged(self):
        rules = cg.PLATFORMS["amazon"]
        r = cg._check("x" * (rules["title_max"] + 5), "short", rules, "en")
        self.assertFalse(r["title_length_ok"])
        self.assertTrue(any("超过" in w for w in r["warnings"]))

    def test_over_long_desc_flagged(self):
        rules = cg.PLATFORMS["tiktok"]
        r = cg._check("ok title", "x" * (rules["desc_max"] + 10), rules, "en")
        self.assertFalse(r["desc_length_ok"])

    def test_placeholder_warning(self):
        r = cg._check("dog bed [ADD: size]", "desc", cg.PLATFORMS["shopee"], "en")
        self.assertTrue(any("[ADD]" in w for w in r["warnings"]))

    def test_all_platforms_have_required_keys(self):
        for key, spec in cg.PLATFORMS.items():
            for field in ("label", "title_max", "desc_max", "highlight_n"):
                self.assertIn(field, spec, f"{key} 缺 {field}")
            self.assertGreater(spec["title_max"], 0)


class TestLanguageGuard(unittest.TestCase):
    def test_chinese_in_english_is_caught(self):
        self.assertIsNotNone(cg._language_issue("Dog bed 狗床 for large dogs", "en"))

    def test_english_in_chinese_is_caught(self):
        self.assertIsNotNone(cg._language_issue("dog bed for large dogs", "zh"))

    def test_pure_english_passes(self):
        self.assertIsNone(cg._language_issue("Dog bed for large dogs", "en"))

    def test_thai_requires_thai_chars(self):
        self.assertIsNotNone(cg._language_issue("Dog bed for large dogs", "th"))
        self.assertIsNone(cg._language_issue("เตียงสุนัข", "th"))

    def test_japanese_requires_kana(self):
        # 只有汉字（中文）不算日语
        self.assertIsNotNone(cg._language_issue("狗床 大型犬", "ja"))
        # 汉字 + 假名才是日语（汉字是日语的一部分，不能当未翻译判掉）
        self.assertIsNone(cg._language_issue("犬用ベッド 大型犬", "ja"))

    def test_chinese_product_name_in_thai_is_caught(self):
        """实测踩过：泰语标题里夹着中文产品名，旧校验只看"有没有泰文"就放行了。"""
        self.assertIsNotNone(
            cg._language_issue("自动喂食器 — [ADD: คุณสมบัติหลัก]", "th"))

    def test_chinese_product_name_in_indonesian_is_caught(self):
        self.assertIsNotNone(cg._language_issue("自动喂食器 — [ADD: atribut]", "id"))

    def test_korean_requires_hangul(self):
        self.assertIsNotNone(cg._language_issue("Dog bed", "ko"))
        self.assertIsNone(cg._language_issue("강아지 침대", "ko"))

    def test_arabic_requires_arabic(self):
        self.assertIsNotNone(cg._language_issue("Dog bed", "ar"))
        self.assertIsNone(cg._language_issue("سرير كلب", "ar"))

    def test_empty_is_issue(self):
        self.assertIsNotNone(cg._language_issue("   ", "en"))

    def test_unknown_language(self):
        self.assertIsNotNone(cg._language_issue("anything", "xx"))


class TestLLMPath(unittest.IsolatedAsyncioTestCase):
    async def test_llm_output_used_when_language_ok(self):
        payload = ('{"title":"Dog Bed for Large Dogs","description":"A comfy bed.",'
                   '"highlights":["a","b","c","d","e"],"seo_keywords":["dog bed"],'
                   '"social_copy":{"hook":"h","pain_point":"p","solution":"s",'
                   '"cta":"c","hashtags":["#dogbed"]},'
                   '"ad_copy":{"headline":"h","body":"b","cta_button":"Shop"}}')
        with mock.patch.object(cg.llm, "available", return_value=True), \
             mock.patch.object(cg.llm, "chat",
                               mock.AsyncMock(return_value=payload)), \
             mock.patch.object(cg.competitor, "run",
                               mock.AsyncMock(return_value=_market_outcome())):
            r = await cg.run("dog bed", target_language="en")
        self.assertTrue(r["success"])
        self.assertIn("llm", r["data"]["generated_by"])
        self.assertEqual(r["data"]["content"]["title"], "Dog Bed for Large Dogs")

    async def test_chinese_output_for_english_retries_then_falls_back(self):
        """模型两次都用中文写英文站 → 必须退回模板，不能把中文当英文交付。"""
        bad = ('{"title":"狗床 大型犬","description":"舒适狗床",'
               '"highlights":["a","b","c","d","e"],"seo_keywords":[]}')
        with mock.patch.object(cg.llm, "available", return_value=True), \
             mock.patch.object(cg.llm, "chat", mock.AsyncMock(return_value=bad)), \
             mock.patch.object(cg.competitor, "run",
                               mock.AsyncMock(return_value=_market_outcome())):
            r = await cg.run("dog bed", target_language="en",
                             retry_on_failure=True)
        self.assertTrue(r["success"])
        d = r["data"]
        self.assertIn("template", d["generated_by"])
        self.assertIsNone(cg._language_issue(d["content"]["title"], "en"))
        # 失败原因要能被看到，不能静默
        self.assertTrue(d["llm_failure_reason"])
        self.assertIn("文案里出现中文", d["llm_failure_reason"])

    async def test_retry_happens_once(self):
        bad = '{"title":"狗床","description":"舒适","highlights":["a"],"seo_keywords":[]}'
        good = ('{"title":"Dog Bed","description":"Comfy.","highlights":["a"],'
                '"seo_keywords":[],"social_copy":{},"ad_copy":{}}')
        calls = {"n": 0}

        async def fake_chat(system, user, max_tokens=800):
            calls["n"] += 1
            return bad if calls["n"] == 1 else good

        with mock.patch.object(cg.llm, "available", return_value=True), \
             mock.patch.object(cg.llm, "chat", mock.AsyncMock(side_effect=fake_chat)), \
             mock.patch.object(cg.competitor, "run",
                               mock.AsyncMock(return_value=_market_outcome())):
            r = await cg.run("dog bed", target_language="en",
                             retry_on_failure=True)
        self.assertEqual(calls["n"], 2)
        self.assertEqual(r["data"]["content"]["title"], "Dog Bed")

    async def test_llm_missing_marketing_falls_back_to_title_not_template(self):
        """LLM 成功但没给社媒/广告时，用标题兜底，不能混入模板的 [ADD] 占位符。

        实测踩过：标题是 LLM 写的英文，社媒文案却是模板的
        "[ADD: product name in English]"，看起来像产品坏了。
        """
        payload = ('{"title":"Automatic Pet Feeder for Cats","description":"Timed meals.",'
                   '"highlights":["a","b","c","d","e"],"seo_keywords":["pet feeder"]}')
        with mock.patch.object(cg.llm, "available", return_value=True), \
             mock.patch.object(cg.llm, "chat", mock.AsyncMock(return_value=payload)), \
             mock.patch.object(cg.competitor, "run",
                               mock.AsyncMock(return_value=_market_outcome())):
            r = await cg.run("自动喂食器", target_language="en")
        d = r["data"]
        self.assertIn("llm", d["generated_by"])
        self.assertEqual(d["marketing"]["social_copy"]["hook"],
                         "Automatic Pet Feeder for Cats")
        self.assertEqual(d["marketing"]["ad_copy"]["headline"],
                         "Automatic Pet Feeder for Cats")
        blob = str(d["marketing"])
        self.assertNotIn("[ADD: product name", blob)
        # 缺了什么必须说出来
        self.assertTrue(any("兜底" in w for w in d["platform_compliance"]["warnings"]))

    async def test_llm_title_placeholder_is_cut_not_just_deleted(self):
        """标题是发布面：`[ADD]` 必须裁掉，且裁完不能凭空多出一条事实。

        实测踩过：模型写 `[ADD: material] BPA-Free Construction`。
        直接删占位符会得到 `BPA-Free Construction`——卖家从没说过 BPA-free，
        这是把"待补"变成了"已声明"。所以只能裁到第一个占位符之前。
        """
        payload = ('{"title":"Automatic Pet Feeder, Timed Dispenser, '
                   '[ADD: capacity] Large Capacity, [ADD: material] BPA-Free",'
                   '"description":"Timed meals.","highlights":["a","b","c","d","e"],'
                   '"seo_keywords":["pet feeder"]}')
        with mock.patch.object(cg.llm, "available", return_value=True), \
             mock.patch.object(cg.llm, "chat", mock.AsyncMock(return_value=payload)), \
             mock.patch.object(cg.competitor, "run",
                               mock.AsyncMock(return_value=_market_outcome())):
            r = await cg.run("自动喂食器", target_language="en")
        title = r["data"]["content"]["title"]
        self.assertEqual(title, "Automatic Pet Feeder, Timed Dispenser")
        self.assertNotIn("[ADD", title)
        self.assertNotIn("BPA", title)          # 未声明的材质不许漏进来
        self.assertTrue(any("标题中的规格占位符已裁掉"
                            in w for w in r["data"]["platform_compliance"]["warnings"]))

    async def test_llm_hashtags_are_kept(self):
        """模型给了话题标签就必须用模型的。

        实测 bug：重建 social 字典后再读 `social["hashtags"]`，
        读到的是新字典里不存在的键 → 模型标签全丢，只剩兜底的 "#Automatic"。
        """
        payload = ('{"title":"Automatic Pet Feeder","description":"Timed meals.",'
                   '"highlights":["a","b","c","d","e"],"seo_keywords":["pet feeder"],'
                   '"social_copy":{"hook":"H","pain_point":"P","solution":"S",'
                   '"cta":"Shop now","hashtags":["#PetFeeder","#CatCare"]},'
                   '"ad_copy":{"headline":"A","body":"B","cta_button":"Shop now"}}')
        with mock.patch.object(cg.llm, "available", return_value=True), \
             mock.patch.object(cg.llm, "chat", mock.AsyncMock(return_value=payload)), \
             mock.patch.object(cg.competitor, "run",
                               mock.AsyncMock(return_value=_market_outcome())):
            r = await cg.run("自动喂食器", target_language="en")
        self.assertEqual(r["data"]["marketing"]["social_copy"]["hashtags"],
                         ["#PetFeeder", "#CatCare"])

    async def test_nested_marketing_shape_also_accepted(self):
        """提示词要顶层 social_copy，下游读 marketing.*——两种形状都必须收。

        实测踩过：两边对不上，模型写得再好社媒/广告也会被当成"未返回"，
        整块走兜底，等于白生成一半内容。
        """
        payload = ('{"title":"Automatic Pet Feeder","description":"Timed meals.",'
                   '"highlights":["a","b","c","d","e"],"seo_keywords":["pet feeder"],'
                   '"marketing":{"social_copy":{"hook":"H","pain_point":"P",'
                   '"solution":"S","cta":"Shop now","hashtags":["#PetFeeder"]},'
                   '"ad_copy":{"headline":"A","body":"B","cta_button":"Shop now"}}}')
        with mock.patch.object(cg.llm, "available", return_value=True), \
             mock.patch.object(cg.llm, "chat", mock.AsyncMock(return_value=payload)), \
             mock.patch.object(cg.competitor, "run",
                               mock.AsyncMock(return_value=_market_outcome())):
            r = await cg.run("自动喂食器", target_language="en")
        d = r["data"]
        self.assertEqual(d["marketing"]["social_copy"]["hook"], "H")
        self.assertEqual(d["marketing"]["ad_copy"]["body"], "B")
        self.assertFalse(any("模型未返回" in w
                             for w in d["platform_compliance"]["warnings"]))

    async def test_hashtag_fallback_uses_phrase_not_first_word(self):
        """兜底标签不能只取标题第一个词——`#Automatic` 没人会搜。"""
        payload = ('{"title":"Automatic Pet Feeder for Cats","description":"Timed.",'
                   '"highlights":["a","b","c","d","e"],"seo_keywords":["pet feeder"]}')
        with mock.patch.object(cg.llm, "available", return_value=True), \
             mock.patch.object(cg.llm, "chat", mock.AsyncMock(return_value=payload)), \
             mock.patch.object(cg.competitor, "run",
                               mock.AsyncMock(return_value=_market_outcome())):
            r = await cg.run("自动喂食器", target_language="en")
        tags = r["data"]["marketing"]["social_copy"]["hashtags"]
        self.assertTrue(tags)
        self.assertEqual(tags[0], "#PetFeeder")
        self.assertNotEqual(tags[0], "#Automatic")

    async def test_truncated_output_keeps_complete_fields(self):
        """输出被截断时不能整包丢弃——前面写好的标题/描述照样能用。

        实测：reasoner 模型在 social_copy 中途被 max_tokens 截断，
        `_extract_json` 返回 None，于是整包退回模板骨架，
        而卖家最需要的标题和描述其实早就写完了。
        """
        truncated = ('{"title":"Automatic Pet Feeder for Cats","description":"Timed meals.",'
                     '"highlights":["a","b","c"],"seo_keywords":["pet feeder"],'
                     '"social_copy":{"hook":"Late dinner agai')
        with mock.patch.object(cg.llm, "available", return_value=True), \
             mock.patch.object(cg.llm, "chat", mock.AsyncMock(return_value=truncated)), \
             mock.patch.object(cg.competitor, "run",
                               mock.AsyncMock(return_value=_market_outcome())):
            r = await cg.run("自动喂食器", target_language="en")
        d = r["data"]
        self.assertIn("llm", d["generated_by"])
        self.assertEqual(d["content"]["title"], "Automatic Pet Feeder for Cats")
        self.assertEqual(d["content"]["description"], "Timed meals.")
        # 截断必须说出来，不能假装是完整产出
        self.assertTrue(any("输出被截断" in w
                            for w in d["platform_compliance"]["warnings"]))

    async def test_salvage_does_not_invent_partial_values(self):
        """截断在字符串中间时，那个字段必须整个不要——补全等于编内容。"""
        truncated = ('{"title":"Automatic Pet Feeder","description":"Timed me')
        got = cg._salvage_json(truncated)
        self.assertEqual(got, {"title": "Automatic Pet Feeder"})
        self.assertNotIn("description", got)

    def test_salvage_returns_none_without_json(self):
        self.assertIsNone(cg._salvage_json("完全不是 JSON"))
        self.assertIsNone(cg._salvage_json(""))

    async def test_default_is_single_attempt(self):
        """默认不重试：单次 LLM 约 90 秒，重试会把接口拖到 3 分钟级。

        这条是**延迟契约**，不是性能优化——超时后客户端拿到的是空响应，
        比拿到一份模板兜底更糟。
        """
        calls = {"n": 0}

        async def fake_chat(system, user, max_tokens=800):
            calls["n"] += 1
            return '{"title":"狗床","description":"舒适","highlights":["a"]}'

        with mock.patch.object(cg.llm, "available", return_value=True), \
             mock.patch.object(cg.llm, "chat", mock.AsyncMock(side_effect=fake_chat)), \
             mock.patch.object(cg.competitor, "run",
                               mock.AsyncMock(return_value=_market_outcome())):
            r = await cg.run("dog bed", target_language="en")
        self.assertEqual(calls["n"], 1)
        self.assertIn("template", r["data"]["generated_by"])
        self.assertTrue(r["data"]["llm_failure_reason"])


class TestMarketFacts(unittest.IsolatedAsyncioTestCase):
    async def test_market_facts_attached_with_provenance(self):
        with mock.patch.object(cg.llm, "available", return_value=False), \
             mock.patch.object(cg.competitor, "run",
                               mock.AsyncMock(return_value=_market_outcome())):
            r = await cg.run("dog bed", category="宠物用品", target_language="en")
        mf = r["data"]["market_facts"]
        self.assertIsNotNone(mf)
        self.assertEqual(mf["source"], "dangdang")
        self.assertEqual(mf["competitor_n"], 3)
        self.assertIn(500, r["data"]["provenance_ids"])

    async def test_market_failure_degrades_with_gap(self):
        bad = ToolOutcome(False, "error", None, "抓取失败")
        with mock.patch.object(cg.llm, "available", return_value=False), \
             mock.patch.object(cg.competitor, "run", mock.AsyncMock(return_value=bad)):
            r = await cg.run("dog bed", category="宠物用品", target_language="en")
        self.assertTrue(r["success"])          # 文案照常生成
        self.assertIsNone(r["data"]["market_facts"])
        self.assertTrue(any("未取到真实竞品数据" in g for g in r["data"]["gaps"]))

    async def test_market_data_can_be_disabled(self):
        with mock.patch.object(cg.llm, "available", return_value=False), \
             mock.patch.object(cg.competitor, "run",
                               mock.AsyncMock(return_value=_market_outcome())) as m:
            r = await cg.run("dog bed", use_market_data=False)
        m.assert_not_called()
        self.assertIsNone(r["data"]["market_facts"])

    async def test_search_term_prefers_product_name_over_category_default(self):
        """红线：竞品检索词必须跟着产品走，不能只用品类默认词。

        实测踩过：产品是「自动喂食器」，品类默认词是 "dog bed"，
        结果拿回来的"竞品价格带"中位数 516 元——那是狗床的价格，
        用它做定位参考等于把文案锚到了错误的市场。
        """
        seen: dict = {}

        async def fake_run(inp):
            seen.update(inp)
            return _market_outcome()

        with mock.patch.object(cg.llm, "available", return_value=False), \
             mock.patch.object(cg.competitor, "run",
                               mock.AsyncMock(side_effect=fake_run)):
            await cg.run("自动喂食器", category="宠物用品", target_language="en")

        self.assertEqual(seen["keyword"], "自动喂食器")
        self.assertNotEqual(seen["keyword"], "dog bed")

    async def test_explicit_keyword_wins(self):
        seen: dict = {}

        async def fake_run(inp):
            seen.update(inp)
            return _market_outcome()

        with mock.patch.object(cg.llm, "available", return_value=False), \
             mock.patch.object(cg.competitor, "run",
                               mock.AsyncMock(side_effect=fake_run)):
            await cg.run("自动喂食器", category="宠物用品", keyword="pet feeder",
                         target_language="en")

        self.assertEqual(seen["keyword"], "pet feeder")


class TestOutputContract(unittest.IsolatedAsyncioTestCase):
    async def _run(self, **kw):
        with mock.patch.object(cg.llm, "available", return_value=False), \
             mock.patch.object(cg.competitor, "run",
                               mock.AsyncMock(return_value=_market_outcome())):
            return await cg.run("dog bed", **kw)

    async def test_frontend_contract_fields(self):
        r = await self._run(target_language="en")
        d = r["data"]
        for key in ("product_name", "category", "platform", "target_language",
                    "content", "marketing", "platform_compliance", "gaps"):
            self.assertIn(key, d)
        for key in ("title", "description", "highlights", "seo_keywords"):
            self.assertIn(key, d["content"])
        for key in ("social_copy", "ad_copy"):
            self.assertIn(key, d["marketing"])
        for h in d["content"]["highlights"]:
            self.assertIn("text", h)
            self.assertIn("icon", h)

    async def test_highlights_never_exceed_platform_limit(self):
        for platform, spec in cg.PLATFORMS.items():
            r = await self._run(platform=platform, target_language="en")
            self.assertLessEqual(len(r["data"]["content"]["highlights"]),
                                 spec["highlight_n"], platform)

    async def test_gaps_are_explicit(self):
        r = await self._run(target_language="en")
        gaps = " ".join(r["data"]["gaps"])
        for word in ("产品规格", "销量", "配置常量", "母语者"):
            self.assertIn(word, gaps)

    async def test_note_declares_generated(self):
        r = await self._run(target_language="en")
        self.assertIn("generated", r["data"]["note"])

    async def test_no_sales_or_rank_claims(self):
        """红线：文案里不得出现销量/排名/促销绝对化用语。"""
        r = await self._run(target_language="en")
        blob = (r["data"]["content"]["title"] + r["data"]["content"]["description"]
                + " ".join(h["text"] for h in r["data"]["content"]["highlights"]))
        for banned in ("best seller", "#1", "free shipping", "销量第一"):
            self.assertNotIn(banned.lower(), blob.lower())


if __name__ == "__main__":
    unittest.main()
