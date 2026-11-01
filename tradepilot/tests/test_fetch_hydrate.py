# -*- coding: utf-8 -*-
"""自适应水合等待的回归测试。

守的是两件事：
1. 提前收工的正确性——正文长度稳定才准走，省下的时间不能以"拿到半渲染页"为代价；
2. 最坏情况不劣化——慢水合页面必须仍等满上限（与原固定 3.5s 行为一致）。
"""
import time
import unittest
from unittest import mock

from app.tools import fetch


class _FakePage:
    """evaluate 返回预设的长度序列；记录真实时间流逝供断言。"""

    def __init__(self, lengths):
        self._lengths = list(lengths)
        self.calls = 0

    def evaluate(self, _expr):
        self.calls += 1
        if isinstance(self._lengths, Exception):
            raise self._lengths
        if len(self._lengths) > 1:
            return self._lengths.pop(0)
        return self._lengths[0]


class TestWaitHydrated(unittest.TestCase):
    def setUp(self):
        # 全部压成小时间片：测试不真等 3.5s
        p = mock.patch.multiple(fetch, _HYDRATE_MAX_S=0.8, _HYDRATE_MIN_S=0.05,
                                _HYDRATE_POLL_S=0.05)
        p.start()
        self.addCleanup(p.stop)

    def test_stable_page_returns_early(self):
        """长度很快稳定（政府站直出页）→ 远早于上限收工。"""
        page = _FakePage([100, 100])  # 第二轮就稳定
        t0 = time.monotonic()
        fetch._wait_hydrated(page)
        elapsed = time.monotonic() - t0
        self.assertLess(elapsed, 0.8, "稳定页应在上限前收工")
        self.assertLessEqual(page.calls, 4, "稳定后不应继续轮询")

    def test_growing_page_waits_full_cap(self):
        """长度持续增长（SPA 慢水合）→ 等满上限，行为不劣于原固定等待。"""

        class Growing(_FakePage):
            def evaluate(self, expr):
                self.calls += 1
                self._n = getattr(self, "_n", 0) + 137
                return self._n

        g = Growing([])
        t0 = time.monotonic()
        fetch._wait_hydrated(g)
        elapsed = time.monotonic() - t0
        self.assertGreaterEqual(elapsed, 0.75, "持续增长应等满上限")
        self.assertLess(elapsed, 3.0, "且不应超过上限太多")

    def test_evaluate_error_returns_immediately(self):
        """页面跳转/销毁导致 evaluate 抛错 → 立即返回，不把异常往外抛。"""
        page = _FakePage([])
        page._lengths = RuntimeError("Target closed")

        class Boom(_FakePage):
            def evaluate(self, expr):
                raise RuntimeError("Target closed")

        b = Boom([])
        t0 = time.monotonic()
        fetch._wait_hydrated(b)          # 不应抛
        self.assertLess(time.monotonic() - t0, 0.7)


class TestConstants(unittest.TestCase):
    """不经过 setUp 补丁，直接锁源码常量。"""

    def test_cap_equals_original_fixed_wait(self):
        """防呆：上限必须等于原固定等待 3.5s——改小等于拿渲染完整性换速度。"""
        self.assertEqual(fetch._HYDRATE_MAX_S, 3.5)
        self.assertGreaterEqual(fetch._HYDRATE_MIN_S, 0.8)
        self.assertLess(fetch._HYDRATE_MIN_S, fetch._HYDRATE_MAX_S)


if __name__ == "__main__":
    unittest.main()
