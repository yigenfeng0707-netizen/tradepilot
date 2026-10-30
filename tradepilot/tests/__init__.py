"""测试包初始化：把数据目录指向临时目录，避免污染仓库内的存证库。

用 unittest 写用例（同时可被 pytest 收集），理由：本机与部分参赛环境没有
pytest 可用，标准库必须能跑——测试跑不起来等于没有测试。
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ["TRADEPILOT_DATA_DIR"] = tempfile.mkdtemp(prefix="tp-tests-")
os.environ["TRADEPILOT_RESPECT_ROBOTS"] = "0"  # 单测不联网
