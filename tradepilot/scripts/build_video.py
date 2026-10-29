# -*- coding: utf-8 -*-
"""TradePilot 演示视频生产线：云端任务实录 → 决策卡帧 → TTS 旁白 → MP4。

流程：
1. 云端跑一次任务（WebSocket），拿决策卡流与最终结果；
2. PIL 渲染每张决策卡 PNG（深蓝底，白卡，微软雅黑）；
3. PowerShell TTS（Huihui Desktop zh-CN）生成旁白 WAV，每帧时长 = 音频时长 + 0.4s；
4. ffmpeg concat 帧 + 混入旁白 + numpy 钢琴 BGM → MP4。

用法：python scripts/build_video.py [输出.mp4]
临时 TTS 脚本一律放 %TEMP%（ASCII 路径，防中文路径 ANSI 坑）。
"""
import asyncio
import json
import math
import os
import subprocess
import sys
import tempfile
import time
import wave

import numpy as np
from PIL import Image, ImageDraw, ImageFont

import httpx
import websockets

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
WS_URL = "wss://fygrobert-tradepilot.ms.show/ws/mission"
W = 1440
H = 900
FRAME_DIR = os.path.join(tempfile.gettempdir(), "tp_frames")
TTS_DIR = os.path.join(tempfile.gettempdir(), "tp_tts")
FFMPEG = r"C:\Program Files\EVCapture\ffmpeg.exe"
FONT = r"C:\Windows\Fonts\msyh.ttc"
FONT_BOLD = r"C:\Windows\Fonts\msyhbd.ttc"

# 深蓝背景 + 白卡配色
BG = (16, 42, 86)
PANEL = (23, 55, 105)
WHITE = (245, 248, 252)
GREY = (150, 168, 190)
ACCENT = (45, 108, 223)
GREEN = (46, 160, 106)
AMBER = (230, 180, 80)


def font(size: int, bold: bool = False):
    return ImageFont.truetype(FONT_BOLD if bold else FONT, size)


def draw_center(draw, y, text, f, color=WHITE):
    bbox = draw.textbbox((0, 0), text, font=f)
    x = (W - (bbox[2] - bbox[0])) // 2
    draw.text((x, y), text, font=f, fill=color)


def wrap_text(draw, text, f, max_w):
    """中文按字符断行。"""
    lines = []
    cur = ""
    for ch in text:
        if ch == "\n":
            lines.append(cur)
            cur = ""
            continue
        if draw.textlength(cur + ch, font=f) > max_w:
            lines.append(cur)
            cur = ch
        else:
            cur += ch
    if cur:
        lines.append(cur)
    return lines


def render_frame(draw, title: str, lines: list[tuple[int, str, str]]) -> None:
    """lines: (size, text, color) 列表，从上往下绘制。"""
    # 顶部品牌条
    draw.rectangle([0, 0, W, 14], fill=ACCENT)
    draw.text((60, 40), "◈ TradePilot · 跨境电商智能运营台", font=font(30, True),
              fill=WHITE)
    draw.text((60, 84), "实录演示 · 每个数字可溯源", font=font(20),
              fill=GREY)
    # 标题
    draw.text((60, 150), title, font=font(46, True), fill=ACCENT)
    # 内容行
    y = 240
    for size, text, color in lines:
        f = font(size)
        for ln in wrap_text(draw, text, f, W - 160):
            draw.text((60, y), ln, font=f, fill=color)
            y += int(size * 1.5)


def save_frame(name: str, img) -> str:
    os.makedirs(FRAME_DIR, exist_ok=True)
    path = os.path.join(FRAME_DIR, name + ".png")
    img.save(path)
    return path


async def run_mission_events() -> dict:
    payload = {
        "goal": "把宠物用品切入美国站，先看真实市场再定价格与合规",
        "keyword": "dog bed", "source": "dangdang", "category": "pet",
        "freight_usd": 2.5, "duty_pct": 7.0, "last_mile_usd": 5.5,
        "unit_cost_cny": None, "target_margin_pct": 20.0,
    }
    events: dict = {"decisions": [], "results": {}}
    async with websockets.connect(WS_URL, extra_headers={"User-Agent": UA}) as ws:
        await ws.send(json.dumps(payload))
        for _ in range(8):
            msg = await asyncio.wait_for(ws.recv(), timeout=200)
            ev = json.loads(msg)
            if ev.get("type") == "decision":
                events["decisions"].append(ev)
            elif ev.get("type") == "mission_done":
                events["results"] = ev.get("report", {}).get("results", {})
                break
    return events


def tts_speech(text: str, wav_path: str) -> None:
    """PowerShell TTS：临时 ps1 放 %TEMP%，纯 ASCII 路径。"""
    ps1 = os.path.join(TTS_DIR, f"tts_{int(time.time() * 1000)}.ps1")
    os.makedirs(TTS_DIR, exist_ok=True)
    content = (
        "$ss = New-Object System.Speech.Synthesis.SpeechSynthesizer\n"
        f"$ss.SetOutputToWaveFile('{wav_path}')\n"
        "$ss.Rate = -2\n"
        f"$ss.Speak('{text.replace("'", "''")}')\n"
        "$ss.Dispose()\n"
    )
    with open(ps1, "w", encoding="utf-16") as f:
        f.write(content)
    subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                    "-File", ps1], check=True, capture_output=True)


def wav_duration(path: str) -> float:
    with wave.open(path, "rb") as w:
        return w.getnframes() / w.getframerate()


def piano_melody() -> np.ndarray:
    """茉莉花旋律（I-V-vi-IV 和声垫），44.1kHz 单声道 float32。"""
    notes = [261.63, 293.66, 329.63, 349.23, 392.00, 440.00, 392.00, 349.23,
             329.63, 293.66, 261.63]
    beats = [0.5, 0.5, 1.0, 0.5, 0.5, 1.0, 0.5, 0.5, 1.0, 0.5, 1.5]
    base = 44100
    total_len = int(sum(beats) * 2 * base)
    out = np.zeros(total_len, dtype=np.float32)
    t_cur = 0
    env_len = 2 * base  # 两拍
    for f, beat in zip(notes, beats):
        n = int(beat * base * 0.9)
        start = int(t_cur * base)
        t = np.arange(n) / base
        env = np.minimum(t, 1.0) * np.exp(-3.5 * np.clip(t - 0.15, 0, 1) * 2.5)
        out[start:start + n] += 0.32 * env * np.sin(2 * np.pi * f * t)
        t_cur += beat
    # 垫音（低八度 IV 和弦持续）
    pad = int(total_len * 0.85)
    t = np.arange(pad) / base
    for f in (130.81, 164.81, 196.00):
        out[:pad] += 0.08 * np.sin(2 * np.pi * f * t) * np.exp(-0.15 * t)
    out = out / (np.abs(out).max() + 1e-9) * 0.55
    return out.astype(np.float32)


def main() -> None:
    print("=== 1. 云端任务实录 ===")
    events = asyncio.run(run_mission_events())
    print(f"决策卡 {len(events['decisions'])} 张，结果步 {len(events['results'])}")

    decisions = events["decisions"]
    results = events["results"]

    # ─── 2. 渲染帧 ───
    print("=== 2. 渲染决策卡帧 ===")
    img_open = Image.new("RGB", (W, H), BG)
    draw_open = ImageDraw.Draw(img_open)
    render_open_card(draw_open, decisions)
    save_frame("00-open", img_open)

    frames = ["00-open"]
    narration = [("00-open", "TradePilot 跨境领航员，跨境电商全链路智能运营智能体。真实数据打底，自主决策，人只看结果。")]

    for idx, dec in enumerate(decisions, 1):
        name = f"{idx:02d}-step"
        img_card = Image.new("RGB", (W, H), BG)
        draw_card = ImageDraw.Draw(img_card)
        render_decision_card(draw_card, dec, results, idx)
        save_frame(name, img_card)
        frames.append(name)
        narration.append((name, decision_narration(idx, dec, results)))

    # ─── 3. TTS 旁白 + 帧时长 ───
    print("=== 3. 生成旁白 ===")
    durations = []
    for name, text in narration:
        wav = os.path.join(TTS_DIR, name + ".wav")
        tts_speech(text, wav)
        dur = wav_duration(wav) + 0.4
        durations.append(dur)
        print(f"  {name}: {dur:.1f}s")

    # 帧时长清单 → concat 文件
    concat = os.path.join(FRAME_DIR, "list.txt")
    with open(concat, "w", encoding="utf-8") as f:
        for name, dur in zip(frames, durations):
            f.write(f"file '{os.path.join(FRAME_DIR, name + '.png')}'\n")
            f.write(f"duration {dur:.2f}\n")
        f.write(f"file '{os.path.join(FRAME_DIR, frames[-1] + '.png')}'\n")

    # 音频：旁白按帧时长补齐（apad 在混音前统一长度）
    print("=== 4. 合成视频 ===")
    out_mp4 = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        tempfile.gettempdir(), "tradepilot_demo.mp4")
    total = sum(durations)
    cmd = [
        FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", concat,
        "-i", os.path.join(TTS_DIR, "00-open.wav"),
        "-filter_complex",
        f"[0:v]scale={W}:{H}[v];"
        f"[1:a]apad=whole_dur={total:.2f},volume=1.2[narr];"
        f"[v][narr]-shortest[out]",
        "-map", "[out]", "-c:v", "libx264", "-preset", "medium", "-crf", "22",
        "-c:a", "aac", "-b:a", "128k", "-r", "24", "-pix_fmt", "yuv420p",
        out_mp4,
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    print(f"✅ 演示视频已生成: {out_mp4}（{total / 60:.1f} 分钟）")


def render_open_card(draw, decisions):
    draw.rectangle([0, 0, W, 14], fill=ACCENT)
    draw.text((60, 40), "◈ TradePilot · 跨境领航员", font=font(52, True),
              fill=WHITE)
    draw.text((60, 120), "跨境电商全链路智能运营智能体", font=font(36, True),
              fill=ACCENT)
    lines = [
        (28, "真实市场扫描 → 现场汇率折算 → 定价沙盘 → 合规体检 → LLM Listing", WHITE),
        (24, "信任底座：多源真实抓取 · 来源闸门 · 存证可溯源 · 诚实口径", GREY),
        (24, f"本次实录：自主规划 {sum(1 for d in decisions if d['reason'].startswith('LLM'))}/{len(decisions)} 步", GREEN),
    ]
    y = 300
    for size, text, color in lines:
        for ln in wrap_text(draw, text, font(size), W - 160):
            draw.text((60, y), ln, font=font(size), fill=color)
            y += int(size * 1.5)
    draw.rectangle([60, y + 30, 300, y + 34], fill=ACCENT)
    draw.text((60, y + 50), "作品：https://fygrobert-tradepilot.ms.show", font=font(22),
              fill=WHITE)


def render_decision_card(draw, dec, results, idx):
    status = dec.get("status", "?")
    gate = dec.get("gate")
    summary = dec.get("summary", "")
    reason = dec.get("reason", "")
    tool = dec.get("decision", "").lstrip("/")
    data = results.get(tool, {}).get("data", {}) if tool in results else {}

    # 标题
    status_color = {"done": GREEN, "held": AMBER, "error": AMBER}[status]
    draw.rectangle([0, 0, W, 14], fill=ACCENT)
    draw.text((60, 40), f"决策 {idx}", font=font(48, True), fill=WHITE)
    draw.text((300, 60), dec.get("decision", ""), font=font(40, True), fill=status_color)
    if gate:
        draw.text((60, 110), f"闸门: {gate} · 状态: {status}", font=font(24), fill=GREY)
    if status != "done":
        draw.text((60, 150), summary, font=font(26), fill=AMBER)
        return

    # 内容明细
    lines = [(30, summary, WHITE)]
    if data:
        y = 260
        for size, text, color in listing_detail_lines(dec, data):
            for ln in wrap_text(draw, text, font(size), W - 160):
                if y > 820:
                    break
                draw.text((60, y), ln, font=font(size), fill=color)
                y += int(size * 1.5)


def listing_detail_lines(dec, data):
    tool = dec.get("decision", "").lstrip("/")
    lines = []
    if tool == "market_scan":
        items = data.get("items", [])[:4]
        for it in items:
            lines.append((24, f"· ¥{it.get('price', 0):.2f}  {it.get('title', '')[:58]}", WHITE))
        lines.append((22, f"来源: {data.get('source_url', '?')}", GREY))
        band = data.get("price_band", {})
        lines.append((24, f"价格带 ¥{band.get('min', 0):.2f} – ¥{band.get('max', 0):.2f}",
                      ACCENT))
    elif tool == "fx_rate":
        lines.append((36, f"USD/CNY = {data.get('rate', 0)}", GREEN))
        lines.append((24, "来源: Frankfurter（欧洲央行参考汇率）· 存证 #" +
                          str(data.get("provenance_id", "?")), GREY))
    elif tool == "pricing_sandbox":
        s = data.get("suggestion", {})
        lines.append((34, f"到岸成本 ${data.get('landed_cost', 0):.2f} → 报价下限 ${data.get('floor_price', 0):.2f}", WHITE))
        lines.append((26, s.get("positioning", ""), GREEN))
        lines.append((22, f"对照口径: {data.get('band_source', '?')}", GREY))
        sens = data.get("sensitivity", [])[:3]
        lines.append((24, "  ".join(f"净利率 {x['margin_pct']}%→${x['floor_price']:.2f}"
                                    for x in sens), WHITE))
    elif tool == "compliance_check":
        checks = data.get("checks", [])[:4]
        for c in checks:
            mark = "✓ 现场验证" if c.get("live") else "· 引用标注"
            lines.append((24, f"· {mark}: {c.get('item', '')[:60]}", WHITE if c.get("live") else GREY))
        lines.append((22, f"来源存证 #" + str(data.get("checks", [{}])[0].get("provenance_id", "?")), GREY))
    elif tool == "listing_draft":
        copy = data.get("copy", {})
        lines.append((28, f"生成方式: {data.get('generated_by', '?')}", GREEN))
        lines.append((26, "Title: " + str(copy.get("title", ""))[:90], WHITE))
        for b in (copy.get("bullets", []) or [])[:2]:
            lines.append((24, "· " + str(b)[:88], GREY))
    return lines


def decision_narration(idx, dec, results):
    tool = dec.get("decision", "").lstrip("/")
    if tool == "market_scan":
        n = results.get("market_scan", {}).get("data", {}).get("items", [])
        return f"第一步，真实市场扫描。从国内零售参考源抓取 {len(n)} 条在售商品，逐条记录价格与来源，绝不编造。"
    if tool == "fx_rate":
        r = results.get("fx_rate", {}).get("data", {}).get("rate", 0)
        return f"第二步，现场汇率。实时获取美元兑人民币汇率 {r:.4f}，来源可存证可复算，不使用任何内嵌经验汇率。"
    if tool == "pricing_sandbox":
        p = results.get("pricing_sandbox", {}).get("data", {})
        return f"第三步，定价沙盘。到岸成本 {p.get('landed_cost', 0):.2f} 美元，报价下限 {p.get('floor_price', 0):.2f} 美元，对照真实价格带给出定位建议。"
    if tool == "compliance_check":
        c = results.get("compliance_check", {}).get("data", {})
        return f"第四步，合规体检。{c.get('live_verified', 0)} 条来源现场验证有效，不可达的官方源如实标注为引用，不假装通过。"
    if tool == "listing_draft":
        return "最后一步，生成上架文案包。事实字段全部回填自存证数据点，标题与卖点可溯源。"
    return "该步骤完成。"


if __name__ == "__main__":
    main()
