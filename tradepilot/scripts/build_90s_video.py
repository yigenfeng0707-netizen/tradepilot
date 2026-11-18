# -*- coding: utf-8 -*-
"""生成赛事硬要求的 90 秒演示视频（用户→场景→产品→证据→生意）。

与 build_video.py 的 28 秒版不同：这一版按 V7.0 PDF 第 9 页要求，
用 10 张卡片讲清用户、场景、产品与真实证据；所有数字来自本次线上任务
的真实 results，不从模板抄。旁白用 edge-tts，失败时按静音帧兜底。
"""
import asyncio
import os
import re
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_video as bv  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT.parent / "报名提交材料包" / "演示视频_90秒.mp4"
FRAME_DIR = Path(tempfile.gettempdir()) / "tp90_frames"
TTS_DIR = Path(tempfile.gettempdir()) / "tp90_tts"
FFMPEG = bv.FFMPEG


def save_frame(name, img):
    FRAME_DIR.mkdir(parents=True, exist_ok=True)
    path = FRAME_DIR / f"{name}.png"
    img.save(path)
    return str(path)


def _num(pattern, text, default="—"):
    m = re.search(pattern, text or "")
    return m.group(1) if m else default


def narrations(decisions, results):
    """每张卡的旁白：短句 + 真实数字，目标总时长约 90 秒。"""
    r = {k: v.get("data", {}) for k, v in results.items()}
    fx = r.get("fx_rate", {}).get("rate", "—")
    market = r.get("market_scan", {})
    items = market.get("items", [])
    band = market.get("price_band", {})
    pricing = r.get("pricing_sandbox", {})
    kp = r.get("keyword_plan", {})
    audit = r.get("listing_audit", {})

    summary = {d.get("decision", "").lstrip("/"): d.get("summary", "") for d in decisions}
    comp = summary.get("compliance_check", "")
    opp = summary.get("opportunity_scan", "")
    kw = summary.get("keyword_plan", "")
    listing = summary.get("listing_draft", "")

    text = [
        ("00-open", "用户是一人公司跨境卖家。一句目标，AI 自主跑完选品到上架。"),
        ("01-step", f"真实抓取{len(items)}条狗床，价格带{band.get('min', '—')}到{band.get('max', '—')}元，每条都有单品页存证。"),
        ("02-step", f"现场汇率{fx}，来源可存证可复算，不用内嵌经验值。"),
        ("03-step", f"到岸{pricing.get('landed_cost', '—')}美元，报价下限{pricing.get('floor_price', '—')}美元，成本参数由使用者入参。"),
        ("04-step", f"四条合规项，{_num(r'(\d+)/4', comp)}条命中官方正文，其余如实标未验证，不假装通过。"),
        ("05-step", f"五个词真实扫描，dog bed 机会分{_num(r'机会分\s*([\d.]+)', opp)}，失败原因逐条列出。"),
        ("06-step", f"竞品标题提取{_num(r'(\d+)\s*个可用词', kw)}个词，Search Terms {_num(r'Search Terms\s*(\d+)', kw)}字节；没有搜索量就不编。"),
        ("07-step", f"标题加五卖点，事实字段回填自存证，{_num(r'(\d+)\s*个存证', listing) or '4'}个点可回访，未填规格明确标注。"),
        ("08-step", f"文案审计{audit.get('score', '—')}分，{audit.get('grade', '—')}级，{audit.get('counts', {}).get('critical', '—')}项必改，不宣称可上架。"),
        ("09-close", "我负责想法和决策，AI 负责把想法变成生意。TradePilot。"),
    ]
    return text


def render_close():
    img = bv.Image.new("RGB", (bv.W, bv.H), bv.BG)
    d = bv.ImageDraw.Draw(img)
    d.rectangle([0, 0, bv.W, 14], fill=bv.ACCENT)
    d.text((60, 60), "从想法到生意", font=bv.font(56, True), fill=bv.WHITE)
    lines = [
        (34, "一个人 + AI = 一支跨境运营部", bv.ACCENT),
        (28, "真实抓取 · 来源闸门 · 逐步决策卡 · 可上架包", bv.WHITE),
        (24, "评委可点「存证#N」回访任何价格与条款原页", bv.GREY),
        (24, "人工基线与事实抽检由真人录入，不估算、不自证", bv.AMBER),
    ]
    y = 210
    for size, text, color in lines:
        for ln in bv.wrap_text(d, text, bv.font(size), bv.W - 160):
            d.text((60, y), ln, font=bv.font(size), fill=color)
            y += int(size * 1.7)
    d.rectangle([60, bv.H - 130, bv.W - 60, bv.H - 126], fill=bv.ACCENT)
    d.text((60, bv.H - 108), f"在线 Demo：{bv.release_info.SHARE_URL}",
           font=bv.font(24), fill=bv.WHITE)
    return img


def synth(text, wav_path):
    os.makedirs(TTS_DIR, exist_ok=True)
    mp3 = os.path.splitext(wav_path)[0] + ".mp3"
    try:
        import edge_tts

        asyncio.run(edge_tts.Communicate(text, "zh-CN-XiaoxiaoNeural").save(mp3))
        subprocess.run([FFMPEG, "-y", "-i", mp3, "-ar", "24000", "-ac", "1", wav_path],
                       check=True, capture_output=True)
        if os.path.getsize(wav_path) > 44:
            return
    except Exception as exc:  # noqa: BLE001
        print(f"edge-tts 失败：{type(exc).__name__}，使用静音帧")
    with wave.open(wav_path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes(b"\x00\x00" * 24000 * 2)


def main():
    print("=== 1. 线上任务实录 ===")
    events = asyncio.run(bv.run_mission_events())
    decisions, results = events["decisions"], events["results"]
    print(f"决策卡 {len(decisions)} 张，结果步 {len(results)}")

    FRAME_DIR.mkdir(parents=True, exist_ok=True)
    TTS_DIR.mkdir(parents=True, exist_ok=True)

    print("=== 2. 渲染卡片 ===")
    open_img = bv.Image.new("RGB", (bv.W, bv.H), bv.BG)
    bv.render_open_card(bv.ImageDraw.Draw(open_img), decisions)
    save_frame("00-open", open_img)
    frames = ["00-open"]
    for idx, dec in enumerate(decisions, 1):
        name = f"{idx:02d}-step"
        img = bv.Image.new("RGB", (bv.W, bv.H), bv.BG)
        bv.render_decision_card(bv.ImageDraw.Draw(img), dec, results, idx)
        save_frame(name, img)
        frames.append(name)
    save_frame("09-close", render_close())
    frames.append("09-close")

    print("=== 3. 旁白与时长 ===")
    texts = narrations(decisions, results)
    durations = []
    for name, text in texts:
        wav = Path(TTS_DIR) / f"{name}.wav"
        synth(text, str(wav))
        with wave.open(str(wav), "rb") as w:
            dur = w.getnframes() / w.getframerate()
        durations.append(dur + 0.35)
        print(f"  {name}: {durations[-1]:.1f}s")
    total = sum(durations)
    if total < 88:
        extra = (88 - total) / len(durations)
        durations = [d + extra for d in durations]
        total = sum(durations)
    print(f"目标总时长 {total:.1f}s")

    concat = FRAME_DIR / "list.txt"
    with open(concat, "w", encoding="utf-8") as f:
        for name, dur in zip(frames, durations):
            f.write(f"file '{FRAME_DIR / (name + '.png')}'\n")
            f.write(f"duration {dur:.2f}\n")
        f.write(f"file '{FRAME_DIR / (frames[-1] + '.png')}'\n")

    print("=== 4. 合成 ===")
    cmd = [
        FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", str(concat),
        "-i", str(Path(TTS_DIR) / "00-open.wav"),
        "-filter_complex",
        f"[0:v]scale={bv.W}:{bv.H}[v];[1:a]apad=whole_dur={total:.2f},volume=1.2[narr]",
        "-map", "[v]", "-map", "[narr]", "-shortest",
        "-c:v", "libx264", "-preset", "medium", "-crf", "22",
        "-c:a", "aac", "-b:a", "128k", "-r", "24", "-pix_fmt", "yuv420p",
        str(OUT),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    print(f"✅ 90 秒演示视频已生成：{OUT}（{total:.1f}s）")


if __name__ == "__main__":
    main()
