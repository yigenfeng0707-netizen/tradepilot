# -*- coding: utf-8 -*-
"""用 Edge 内核填写 OPC 海南城市赛官方报名表。

身份字段只从环境变量读取，不写入仓库、不落日志：
  REG_CONTACT  联系人姓名
  REG_PHONE    手机或微信
  REG_EMAIL    邮箱

用法：
  REG_CONTACT=... REG_PHONE=... REG_EMAIL=... python scripts/fill_registration.py
  # 加 --submit 才真正点提交；默认只预填并截图供确认。
"""
import os
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
MATERIALS = ROOT / "报名提交材料包"
ZIP = ROOT / "报名提交材料包.zip"
SHOT = MATERIALS / "报名表单_预填截图.png"
RECEIPT = MATERIALS / "回执"
URL = ("https://hub.opcquan.com/contests/event-b04af338f9e1/apply"
       "?community=f0a66104-c8bd-4042-b88b-e6cdaa129860")

SUBJECT = "TradePilot（独立开发者 1 人）"
PROJECT = "跨境领航员 TradePilot"
TRACK = "AI for Business"  # 用户 11-18 决定：按主赛道先提交


def intro_text() -> str:
    p = MATERIALS / "作品简介.md"
    text = p.read_text(encoding="utf-8") if p.exists() else ""
    lines = [ln.strip() for ln in text.splitlines()
             if ln.strip() and not ln.strip().startswith("#")]
    text = "\n".join(lines).replace("**", "").replace("`", "")
    return text[:1900]


def main():
    contact = os.environ.get("REG_CONTACT", "").strip()
    phone = os.environ.get("REG_PHONE", "").strip()
    email = os.environ.get("REG_EMAIL", "").strip()
    if not (contact and phone and email):
        raise SystemExit("缺少环境变量 REG_CONTACT / REG_PHONE / REG_EMAIL；不会写入仓库。")
    if not ZIP.exists():
        raise SystemExit(f"缺少上传包 {ZIP}，先运行 scripts/build_submission_kit.py")

    do_submit = "--submit" in sys.argv
    headed = "--headed" in sys.argv

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=not headed)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.goto(URL, timeout=90000)
        page.wait_for_load_state("networkidle")

        # 非敏感字段：全部来自已定口径，不手抄数字
        inputs = page.locator("input.o-input")
        # 顺序：0 参赛主体，1 项目名称，2 联系人，3 联系方式，4 邮箱，5 演示链接
        inputs.nth(0).fill(SUBJECT)
        inputs.nth(1).fill(PROJECT)
        inputs.nth(2).fill(contact)
        inputs.nth(3).fill(phone)
        inputs.nth(4).fill(email)
        page.get_by_role("combobox", name="赛道").click()
        page.get_by_role("option", name=TRACK).click()
        page.locator('input[type="url"]').fill(
            "https://gsym236998-tradepilot-opc.ms.show")
        page.locator("textarea").fill(intro_text())
        page.locator('input[type="file"][multiple]').set_input_files(str(ZIP))
        cb = page.locator('input[type="checkbox"]')
        # 该 checkbox 是视觉隐藏的 sr-only 元素，Playwright 无法滚动到视口内；
        # 用 DOM click 触发同一 React 状态，并立即核验 checked。
        cb.evaluate("(el) => el.click()")
        if not cb.is_checked():
            raise SystemExit("真实性确认勾选失败，停止（不提交）")

        page.screenshot(path=str(SHOT), full_page=True)
        print("预填完成，截图：", SHOT)
        print("身份字段来自环境变量，未写入任何文件。")

        if do_submit:
            page.get_by_role("button", name="提交报名").click()
            page.wait_for_timeout(5000)
            RECEIPT.mkdir(exist_ok=True)
            receipt_shot = RECEIPT / "提交后页面.png"
            page.screenshot(path=str(receipt_shot), full_page=True)
            print("已点击提交。最终 URL：", page.url)
            print("回执截图：", receipt_shot)
        else:
            print("未提交（默认 dry-run）。确认后加 --submit 重跑。")
            time.sleep(1)
        browser.close()


if __name__ == "__main__":
    main()
