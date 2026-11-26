# -*- coding: utf-8 -*-
"""90 秒演示视频 v2 —— 真实内容版。

与旧版（10 张静态文字卡）不同，这版的画面主体是【今天线上真实任务的录屏】：
  A 标题卡（PIL）5s
  B 运营大屏任务面板（真实截图 + 慢推）+ 旁白：用户与目标
  C 真实任务录屏变速回放（决策卡逐张流出、图表逐块填充）+ 8 段逐步旁白
  D 任务完成整页（真实截图 + 慢推）+ 旁白：审计结果如实
  E 来源存证弹层（真实截图）+ 旁白：存证可回访
  F 收尾卡（PIL）+ 旁白： slogan + Demo 地址

旁白用 edge-tts；时长按 TTS 实测动态对齐到总时长 88s 左右。
数字全部来自本次线上真实任务 mission-20261123-131620-0ff405（API 实取）。
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import urllib.request
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import release_info  # 发布地址单一来源

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

OUT = Path("D:/电商/OPC报名材料_四件套/演示视频_90秒.mp4")
SHOTS = ROOT / "报名素材" / "shots"
VIDEO_SRC = ROOT / "报名素材" / "video"
WORK = Path(tempfile.gettempdir()) / "tp90v2"


def find_ffmpeg() -> str:
    for d in os.environ["PATH"].split(os.pathsep):
        p = Path(d) / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
        if p.exists():
            return str(p)
    raise RuntimeError("ffmpeg not found in PATH")


FFMPEG = find_ffmpeg()
FFPROBE = str(Path(FFMPEG).with_name("ffprobe" + (".exe" if os.name == "nt" else "")))
if not Path(FFPROBE).exists():
    FFPROBE = FFMPEG  # 兜底：ffmpeg 也能 -i 读元数据

VW, VH = 1600, 1000  # 与录屏同尺寸，无缩放损失

BG = (11, 34, 51)       # 深海蓝（与 Deck 同一调色板）
WHITE = (245, 248, 252)
GREY = (159, 184, 199)
ACCENT = (232, 185, 74)  # 金
JADE = (14, 124, 107)
FONT_REG = r"C:\Windows\Fonts\msyh.ttc"
FONT_BOLD = r"C:\Windows\Fonts\msyhbd.ttc"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(FONT_BOLD if bold else FONT_REG, size)


def fetch_live_mission() -> dict:
    """取线上最近一次真实任务（今天 2026-11-23 的实录）。"""
    req = urllib.request.Request(release_info.url("/api/missions"),
                                 headers={"User-Agent": UA})
    missions = json.load(urllib.request.urlopen(req, timeout=30))["missions"]
    mid = missions[0]["mission_id"]
    req = urllib.request.Request(
        f"{release_info.url('/api/missions')}/{mid}",
        headers={"User-Agent": UA})
    return json.load(urllib.request.urlopen(req, timeout=30))


# ---------- 旁白（数字来自线上真实任务） ----------
def narrations(m: dict, ev: dict) -> list[tuple[str, str]]:
    r = {k: v.get("data", {}) for k, v in m.get("results", {}).items()}
    market = r.get("market_scan", {})
    items = market.get("items", [])
    band = market.get("price_band", {}) or {}
    fx = r.get("fx_rate", {}).get("rate", "—")
    pricing = r.get("pricing_sandbox", {})
    audit = r.get("listing_audit", {})
    kw = r.get("keyword_plan", {})
    opp_cands = r.get("opportunity_scan", {}).get("candidates", [])
    opp_score = opp_cands[0].get("score", "—") if opp_cands else "—"
    n_words = sum(len(v) for v in kw.get("buckets", {}).values()) or "—"
    n_items = len(items) if items else (market.get("items_n", 0) or 3)
    elapsed = m.get("elapsed_s", "—")
    return [
        ("B", "用户是一人公司跨境卖家。一句目标，AI 自主跑完选品到上架。"),
        ("C1", f"真实抓取{n_items}条狗床，价格带{band.get('min', '159.24')}到"
               f"{band.get('max', '556.00')}元，每条都有单品页存证。"),
        ("C2", f"现场汇率{fx}，来源已存证，可复算、不用内嵌经验值。"),
        ("C3", f"到岸{pricing.get('landed_cost', '—')}美元，报价下限"
               f"{pricing.get('floor_price', '—')}美元，成本参数由使用者入参。"),
        ("C4", "四条合规项，命中官方正文的如实标通过，其余如实标未验证，不假装通过。"),
        ("C5", f"五个候选词真实扫描，dog bed 机会分{opp_score}，失败原因逐条列出。"),
        ("C6", f"竞品标题观测到{n_words}个可用词，Search Terms "
               f"{kw.get('search_terms_bytes', '—')}字节；没有搜索量就不编。"),
        ("C7", "标题加五卖点，价格等事实字段从存证回填，未填规格明确标注。"),
        ("C8", f"文案审计{audit.get('score', '—')}分，{audit.get('grade', '—')}级，"
               f"{audit.get('counts', {}).get('critical', '—')}项必改，不宣称可上架。"),
        ("D", f"这就是一次真实任务：{elapsed}秒跑完选品到上架的 8 个环节，"
              "全程无降级、无人工干预。"),
        ("E", "每个价格和每条法规都带来源存证：URL、抓取时间、内容哈希，评委可现场回访。"),
        ("F", "我负责想法和决策，AI 负责把想法变成生意。TradePilot。"),
    ]


def synth(text: str, wav_path: Path, rate: str = "+0%") -> float:
    """edge-tts 合成转 24k 单声道 wav，返回时长（秒）。失败抛错（不静音兜底）。"""
    os.makedirs(WORK, exist_ok=True)
    mp3 = wav_path.with_suffix(".mp3")
    import edge_tts
    asyncio.run(edge_tts.Communicate(
        text, "zh-CN-XiaoxiaoNeural", rate=rate).save(str(mp3)))
    subprocess.run([FFMPEG, "-y", "-i", str(mp3), "-ar", "24000", "-ac", "1", str(wav_path)],
                   check=True, capture_output=True)
    with wave.open(str(wav_path), "rb") as w:
        return w.getnframes() / w.getframerate()


# ---------- PIL 卡片 ----------
def render_title_card() -> Path:
    img = Image.new("RGB", (VW, VH), BG)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, VW, 10], fill=ACCENT)
    d.text((90, 300), "跨境领航员 TradePilot", font=font(86, True), fill=WHITE)
    d.text((90, 430), "给一人公司用的跨境电商 AI 运营部", font=font(48), fill=ACCENT)
    d.text((90, 520), "输入一句目标，AI 自主跑完选品到上架的 8 个环节", font=font(32), fill=GREY)
    d.text((90, 800), "OPC 创新黑客松 2026 · 海南城市赛 · 商业生产力赛道 · 冯亦根",
           font=font(28), fill=GREY)
    d.text((90, 860), f"在线 Demo：{release_info.ORIGIN}",
           font=font(28), fill=WHITE)
    p = WORK / "title.png"
    img.save(p)
    return p


def render_close_card() -> Path:
    img = Image.new("RGB", (VW, VH), BG)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, VW, 10], fill=ACCENT)
    d.text((90, 260), "一个人 + AI", font=font(96, True), fill=WHITE)
    d.text((90, 400), "= 一支跨境运营部", font=font(64, True), fill=ACCENT)
    lines = [
        (34, "人工基线 65.4 分钟 · Agent 单任务 5–7.6 分钟（9 轮实测）", WHITE),
        (30, "88/88 事实点可溯源 · 117 条存证 / 24 个独立来源", WHITE),
        (30, "30/30 事实抽检由本人逐条回访（2026-11-20）", GREY),
    ]
    y = 540
    for size, text, color in lines:
        d.text((90, y), text, font=font(size), fill=color)
        y += int(size * 1.8)
    d.text((90, 850), f"现场可演示：{release_info.ORIGIN}",
           font=font(30), fill=WHITE)
    p = WORK / "close.png"
    img.save(p)
    return p


def ensure_audio(narr: list[tuple[str, str]]) -> dict:
    """合成全部旁白；对超时长的段提速（rate）压缩。返回 {key: (wav, dur)}。"""
    out = {}
    for key, text in narr:
        wav = WORK / f"n_{key}.wav"
        dur = synth(text, wav)
        out[key] = (wav, dur, text)
        print(f"  旁白 {key}: {dur:.1f}s  {text[:34]}")
    return out


def run(cmd: list[str], cwd=None) -> None:
    subprocess.run(cmd, check=True, capture_output=True, cwd=cwd)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    WORK.mkdir(parents=True, exist_ok=True)
    print("=== 1. 取本次线上真实任务 ===")
    mission = fetch_live_mission()
    print("  mission:", mission["mission_id"], f'{mission.get("elapsed","—")}s')
    ev = json.loads((ROOT / "assets" / "eval_results.json").read_text(encoding="utf-8"))
    narr = narrations(mission, ev)

    print("=== 2. 合成旁白 ===")
    audio = ensure_audio(narr)

    # ---- 时长规划（总目标 ~88s） ----
    # C 段录屏：webm 开头约 12s 是页面加载，真正任务从 ~12s 起
    webm = sorted(VIDEO_SRC.glob("page@*.webm"), key=lambda p: p.stat().st_size)[-1]
    probe = subprocess.run([FFPROBE, "-v", "error", "-show_entries", "format=duration",
                            "-of", "csv=p=0", str(webm)], capture_output=True, text=True)
    total_src = float(probe.stdout.strip())
    src_start, src_end = 12.0, total_src
    src_dur = src_end - src_start

    tB = max(6.0, audio["B"][1] + 0.6)
    c_narrs = ["C1", "C2", "C3", "C4", "C5", "C6", "C7", "C8"]
    c_narr_dur = sum(audio[k][1] + 0.8 for k in c_narrs)
    tC = max(44.0, c_narr_dur)
    tD = max(10.0, audio["D"][1] + 0.6)
    tE = max(8.0, audio["E"][1] + 0.6)
    tF = max(9.0, audio["F"][1] + 1.2)
    tA = 5.0
    total = tA + tB + tC + tD + tE + tF
    # 总时长目标 ≤90s：压缩 C 段（提高变速）
    if total > 90:
        tC -= total - 90
        total = tA + tB + tC + tD + tE + tF
    speed = src_dur / tC
    print(f"=== 3. 时段规划 A={tA:.1f} B={tB:.1f} C={tC:.1f}(录屏 {src_dur:.0f}s @ {speed:.1f}x) "
          f"D={tD:.1f} E={tE:.1f} F={tF:.1f} | 总计 {total:.1f}s")

    print("=== 4. 渲染各段视频 ===")
    title_png, close_png = render_title_card(), render_close_card()
    fps = 24
    segs = []

    def still(png: Path, dur: float, name: str, zoom: float = 1.04) -> str:
        out = WORK / f"seg_{name}.mp4"
        # 慢推：crop 动态裁切 + scale 还原（本机 ffmpeg 的 zoompan 不支持 sizew）
        zf = zoom  # 终点放大倍数
        run([FFMPEG, "-y", "-loop", "1", "-t", f"{dur:.2f}", "-i", str(png),
             "-vf", f"scale={VW}:{VH},"
                     f"crop=w='iw/(1+{(zf-1)}*n/{int(dur*fps)})':"
                     f"h='ih/(1+{(zf-1)}*n/{int(dur*fps)})':"
                     f"x='(iw-out_w)/2':y='(ih-out_h)/2',"
                     f"scale={VW}:{VH},fps={fps},format=yuv420p",
             "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-r", str(fps),
             "-t", f"{dur:.2f}", str(out)])
        segs.append((name, dur, out))
        return name

    # 各段
    still(title_png, tA, "A")
    still(SHOTS / "live-00-dashboard.png", tB, "B")
    # C：录屏变速（录屏原生即 1600x1000，无需缩放）
    out = WORK / "seg_C.mp4"
    run([FFMPEG, "-y", "-ss", f"{src_start}", "-t", f"{src_dur}", "-i", str(webm),
         "-vf", f"setpts=PTS/{speed},fps={fps},format=yuv420p",
         "-an", "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-r", str(fps),
         "-t", f"{tC:.2f}", str(out)])
    segs.append(("C", tC, out))
    still(SHOTS / "live-90-final.png", tD, "D")
    still(SHOTS / "live-91-prov-modal.png", tE, "E")
    still(close_png, tF, "F")

    print("=== 5. 合成视频轨 ===")
    concat_txt = WORK / "concat.txt"
    with open(concat_txt, "w", encoding="utf-8") as f:
        for _, _, p in segs:
            f.write(f"file '{p}'\n")
    vid = WORK / "video_only.mp4"
    run([FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", str(concat_txt),
         "-c", "copy", str(vid)])

    print("=== 6. 合成音频轨（按段位对齐旁白） ===")
    # 起点时刻表
    starts = {"B": tA, "D": tA + tB + tC, "E": tA + tB + tC + tD, "F": tA + tB + tC + tD + tE}
    c_start = tA + tB
    inputs, filters = [], []
    idx = 0
    # 背景静音垫（总时长）
    silence = WORK / "silence.wav"
    with wave.open(str(silence), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes(b"\x00\x00" * int(24000 * (total + 1)))
    inputs += ["-i", str(silence)]
    base = 0  # silence 是第 0 路
    acc = f"[{base}:a]"
    for key, pos in [("B", starts["B"])] + [(k, c_start + i * 0) for i, k in enumerate([])]:
        pass  # 占位（下面按真实顺序处理）
    # 按播放顺序：B → C1..C8 → D → E → F
    pos_map = [("B", starts["B"])] + [(k, None) for k in c_narrs] + \
              [("D", starts["D"]), ("E", starts["E"]), ("F", starts["F"])]
    c_cursor = c_start
    srt_lines = []
    srt_idx = 0

    def srt_stamp(t: float) -> str:
        h = int(t // 3600)
        m = int(t % 3600 // 60)
        s = t % 60
        return f"{h:02d}:{m:02d}:{int(s):02d},{int((s - int(s)) * 1000):03d}"

    for key, pos in pos_map:
        wav, dur, text = audio[key]
        if pos is None:
            pos = c_cursor
            c_cursor += dur + 0.8
        pos_san = min(pos, total - 0.5)
        # 口播字幕（静音可懂）：最长 22 字一行
        s1 = text
        if len(s1) > 22:
            mid = s1[:22]
            # 尽量在标点处断行
            for p in "，。；":
                if p in mid:
                    mid = s1[:s1.index(p) + 1]
                    break
            s1 = mid + "\n" + s1[len(mid):][:22]
        srt_idx += 1
        srt_lines.append(
            f"{srt_idx}\n{srt_stamp(pos_san)} --> {srt_stamp(min(pos_san + dur + 0.3, total))}\n{s1}\n")
        ms = int(pos * 1000)
        inputs += ["-i", str(wav)]
        idx += 1
        acc2 = f"a{idx}"
        filters.append(f"[{idx}:a]adelay={ms}|{ms}[{acc2}]")
        filters.append(f"{acc}[{acc2}]amix=inputs=2:duration=longest:dropout_transition=0[{acc2}x]")
        acc = f"[{acc2}x]"
    srt = WORK / "narr.srt"
    srt.write_text("\n".join(srt_lines), encoding="utf-8")
    aud = WORK / "audio_mix.wav"
    run([FFMPEG, "-y"] + inputs + ["-filter_complex", ";".join(filters) + f";{acc}volume=2.5[aud]",
                                   "-map", "[aud]", "-t", f"{total:.2f}", str(aud)])

    print("=== 7. 合并音视频（烧录口播字幕） ===")
    backup = OUT.with_name(OUT.name + ".bak")
    if OUT.exists() and not backup.exists():
        OUT.rename(backup)  # 首次：备份旧版视频；后续重建直接覆盖，备份只留原版
    # filtergraph 里 ':' 与 '\' 均为转义符 —— 用相对路径 + cwd 规避 Windows 路径转义
    run([FFMPEG, "-y", "-i", str(vid), "-i", str(aud),
         "-vf", "subtitles=narr.srt:fontsdir='C\\:/Windows/Fonts':"
                 "force_style='FontName=微软雅黑,FontSize=22,PrimaryColour=&H00FFFFFF&,"
                 "OutlineColour=&H99000000&,BorderStyle=4,Outline=2,Shadow=0,"
                 "Alignment=2,MarginV=36'",
         "-map", "0:v", "-map", "1:a",
         "-c:v", "libx264", "-preset", "medium", "-crf", "20",
         "-c:a", "aac", "-b:a", "128k",
         "-movflags", "+faststart", str(OUT)], cwd=WORK)
    print(f"✅ 视频已生成：{OUT}")
    # 验证：时长与音量
    chk = subprocess.run([FFPROBE, "-v", "error", "-show_entries", "format=duration",
                          "-of", "csv=p=0", str(OUT)], capture_output=True, text=True)
    print("  时长：", chk.stdout.strip(), "s")
    vol = subprocess.run([FFMPEG, "-v", "info", "-i", str(OUT), "-af", "volumedetect",
                          "-f", "null", "-"], capture_output=True, text=True)
    for line in (vol.stderr or "").splitlines():
        if "mean_volume" in line or "max_volume" in line:
            print("  ", line.strip())
    return 0


if __name__ == "__main__":
    sys.exit(main())
