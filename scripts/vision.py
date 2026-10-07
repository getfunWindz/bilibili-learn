"""第三条路径：多模态大模型 API 视觉转写（OpenAI 兼容）

当字幕不可信且音频无人声（whisper 转写为空/覆盖不足）时，
抽帧交给多模态大模型（支持视觉的 chat 模型）转写画面内容，
再交由 harness 内 LLM 按 skill 规则总结。
"""
import base64
import io
import re
import requests

DEFAULT_PROMPT = (
    "你是视频内容转写助手。以下是一段视频按时间顺序抽取的画面帧（每帧标注时间戳）。"
    "请按帧顺序详细描述每帧画面内容：画面中的文字、图表、代码、操作步骤、关键信息都要尽量完整转录。"
    "输出格式：每行一条 `[时间戳秒] 描述`。如果多帧内容连续，可合并描述并标注起止时间。"
    "请用中文输出。"
)

CHECK_PROMPT = (
    "你在帮一个学习报告工具做「转写资料复检」。"
    "以下是视频某区间的转写文本（可能不完整），以及该区间每秒抽取的画面帧（标注时间戳）。"
    "请判断画面中是否有转写文本**未覆盖**的知识材料（如白板公式、PPT 要点、代码、演示步骤、字幕卡）。\n"
    "输出规则：\n"
    "- 若有遗漏：逐条输出 `[时间戳秒] 补充内容`（只写遗漏的材料，不重复转写已有内容）\n"
    "- 若画面只是人物讲话/空镜头/与转写一致的板书：只输出四个字 `无遗漏`"
)

PLAN_PROMPT = (
    "你是学习报告工具的复检规划助手。以下是教学视频的完整转写文本（带时间戳）。\n"
    "请找出最需要「画面复检」的区间——即画面中很可能含有转写未覆盖的知识材料"
    "（白板公式 / PPT 要点 / 代码 / 图表 / 演示操作）。\n"
    "**重点标记转写中出现引导性字眼的位置**，如：「根据」「看一下」「如图」「如图所示」「这张图/表」"
    "「表格」「演示一下」「大家注意」「我们来看」等——这些位置画面往往有重要信息。\n"
    "区间长度约 20 秒，但**不必固定**，可根据内容自行调整（10~60 秒均可）；最多挑 {max_ranges} 个区间。\n"
    "输出格式（每行一条，只输出区间行，不要其他说明）：\n"
    "`[起始秒-结束秒] 挑选理由`\n"
    "若确实没有值得复检的区间，输出：`无区间`"
)

MATCH_PROMPT = (
    "你是学习报告工具的「字幕归属校验」助手。以下是同一个视频在开头/中间/结尾三个时间点的画面帧，"
    "以及待校验字幕在开头/中间/结尾的片段。\n"
    "请判断这些字幕是否属于这个视频：\n"
    "- 主题/领域是否一致（画面中的板书、PPT 标题、演示内容与字幕是否相关）\n"
    "- 字幕是否明显来自其他视频（如影视剧台词、歌词、其他领域的教程、完全无关的口播）\n"
    "只输出一行结论：`一致` 或 `不一致：简短理由`。不要输出其他内容。"
)

class VisionError(Exception):
    """多模态 API 相关错误"""


def extract_frames(video_path: str, interval_sec: int = 10, max_frames: int = 24) -> list:
    """用 PyAV 抽帧 → [(timestamp_sec, jpeg_bytes)]"""
    import av
    container = av.open(video_path)
    try:
        stream = container.streams.video[0]
        fps = float(stream.average_rate or 25.0)
        step = max(1, int(interval_sec * fps))
        frames = []
        for i, frame in enumerate(container.decode(stream)):
            if i % step != 0:
                continue
            ts = int(i / fps)
            img = frame.to_image().convert("RGB")
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=70)
            frames.append((ts, buf.getvalue()))
            if len(frames) >= max_frames:
                break
        return frames
    finally:
        container.close()


def _encode_data_uri(jpeg: bytes) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()


def describe_video(cfg_vision: dict, frames: list, prompt: str = None) -> str:
    """调 OpenAI 兼容 chat completions（多模态）→ 画面转写文本"""
    if not cfg_vision.get("enabled"):
        raise VisionError("多模态 API 未启用：请在 config.json 的 vision 块设置 enabled=true")
    if not cfg_vision.get("api_key") or not cfg_vision.get("base_url"):
        raise VisionError("多模态 API 未配置完整：config.json 的 vision 块需要 base_url/api_key/model")
    url = cfg_vision["base_url"].rstrip("/") + "/chat/completions"
    content = [{"type": "text", "text": prompt or DEFAULT_PROMPT}]
    for ts, jpeg in frames:
        content.append({"type": "text", "text": f"[帧时间 {ts}s]"})
        content.append({"type": "image_url", "image_url": {"url": _encode_data_uri(jpeg)}})
    payload = {
        "model": cfg_vision.get("model", "gpt-4o-mini"),
        "messages": [{"role": "user", "content": content}],
        "max_tokens": 4096,
    }
    r = requests.post(url, json=payload,
                      headers={"Authorization": f"Bearer {cfg_vision['api_key']}"},
                      timeout=300, trust_env=False)
    r.raise_for_status()
    data = r.json()
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise VisionError(f"多模态 API 返回格式异常：{str(data)[:200]}")


def vision_transcribe(cfg_vision: dict, video_path: str, prompt: str = None) -> str:
    """完整流程：抽帧 → 多模态转写 → 文本"""
    frames = extract_frames(video_path,
                            cfg_vision.get("frame_interval", 10),
                            cfg_vision.get("max_frames", 24))
    if not frames:
        raise VisionError("视频抽帧失败（无视频流或视频不可解码）")
    return describe_video(cfg_vision, frames, prompt)


def find_content_gaps(lines: list, duration: float, min_gap: float = 5.0) -> list:
    """找转写空档区间 [(start, end)]：相邻行间及末尾超过 min_gap 的空白段"""
    gaps = []
    prev_end = 0.0
    for ln in lines:
        s = float(ln["start"])
        if s - prev_end >= min_gap:
            gaps.append((prev_end, s))
        prev_end = max(prev_end, float(ln["end"]))
    if duration and float(duration) - prev_end >= min_gap:
        gaps.append((prev_end, float(duration)))
    return gaps


def pick_check_intervals(lines: list, duration: float, min_gap: float = 5.0) -> list:
    """复检区间：转写为空 → 全视频；否则 → 转写空档"""
    if not lines:
        return [(0.0, float(duration or 0))]
    return find_content_gaps(lines, duration, min_gap)


def extract_frames_range(video_path: str, start_sec: float, end_sec: float,
                         fps: int = 1, max_frames: int = 30) -> list:
    """区间内按 fps 抽帧（默认每秒 1 帧）→ [(timestamp_sec, jpeg_bytes)]"""
    import av
    container = av.open(video_path)
    try:
        stream = container.streams.video[0]
        frames = []
        last_ts = None
        min_delta = (1.0 / max(1, fps)) - 0.01
        for frame in container.decode(stream):
            ts = float(frame.pts * stream.time_base) if frame.pts is not None else start_sec
            if ts < start_sec - 0.5:
                continue
            if ts > end_sec:
                break
            if last_ts is not None and ts - last_ts < min_delta:
                continue
            img = frame.to_image().convert("RGB")
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=70)
            frames.append((int(ts + 0.5), buf.getvalue()))
            last_ts = ts
            if len(frames) >= max_frames:
                break
        return frames
    finally:
        container.close()


def plan_check_intervals(cfg_vision: dict, lines: list, duration: float,
                         max_ranges: int = 5) -> list:
    """模型规划复检区间（知识点丰富/含引导性字眼区域）→ [(start, end, reason)]；
    模型不可用/解析失败时抛异常（由调用方回退机械规则）"""
    ctx = "\n".join(f"[{int(l['start'])}-{int(l['end'])}] {l['text']}" for l in (lines or [])[:400])
    if not ctx:
        return []
    text = describe_video(cfg_vision, [], prompt=PLAN_PROMPT.format(max_ranges=max_ranges) +
                          f"\n\n【完整转写文本】\n{ctx}")
    if "无区间" in text and not re.search(r"\[\d", text):
        return []
    ranges = []
    for m in re.finditer(r"\[(\d+)\s*[-~]\s*(\d+)\]\s*(.*)", text):
        s, e = int(m.group(1)), int(m.group(2))
        if e > s:
            ranges.append((float(s), float(e), m.group(3).strip()))
    if not ranges:
        raise VisionError(f"区间规划解析失败：{text[:120]}")
    return ranges[:max_ranges]


def check_transcript(cfg_vision: dict, video_path: str, lines: list, duration: float,
                     fps: int = 1, max_frames: int = 30, min_gap: float = 5.0,
                     max_rounds: int = 2, max_ranges: int = 5) -> dict:
    """转写资料复检（固定路径，多轮）：
    ① 模型规划复检区间（引导性字眼/知识点丰富区）——失败时回退机械空档规则
    ② 区间每秒抽帧 → 多模态判断遗漏 → 补充
    ③ 本轮有新发现且未达 max_rounds → 再规划下一轮；无新发现则结束
    返回 {supplements: [{start,end,text}], raw: str}；所有区间空时全视频抽帧（转写为空场景）"""
    supplements = []
    raws = []
    planned = set()
    ctx = "\n".join(f"[{int(l['start'])}-{int(l['end'])}] {l['text']}" for l in (lines or [])[:200])
    for round_i in range(max(1, max_rounds)):
        # ① 规划区间（模型优先；失败/首轮无结果时回退机械规则）
        intervals = []
        try:
            planned_ranges = plan_check_intervals(cfg_vision, lines, duration, max_ranges)
            intervals = [(s, e) for s, e, _r in planned_ranges]
        except Exception:
            pass
        if not intervals:
            fallback = pick_check_intervals(lines, duration, min_gap)
            if not lines:
                fallback = [(0.0, float(duration or 0))]  # 转写为空 → 全视频
            intervals = [iv for iv in fallback if iv not in planned]
        intervals = [iv for iv in intervals if iv not in planned and iv[1] - iv[0] >= 0.5]
        if not intervals:
            break
        got_new = False
        for (s, e) in intervals:
            planned.add((s, e))
            frames = extract_frames_range(video_path, s, e, fps=fps, max_frames=max_frames)
            if not frames:
                continue
            prompt = CHECK_PROMPT + (f"\n\n【已有转写文本（供对照，勿重复）】\n{ctx}" if ctx else "")
            text = describe_video(cfg_vision, frames, prompt=prompt)
            raws.append(text)
            if "无遗漏" in text:
                continue
            for m in re.finditer(r"\[(\d+)(?:[-~](\d+))?\]\s*(.+)", text):
                st = int(m.group(1))
                en = int(m.group(2)) if m.group(2) else st + 1
                supplements.append({"start": float(st), "end": float(en), "text": m.group(3).strip()})
                got_new = True
        if not got_new:
            break  # 本轮无新发现 → 结束（一次效果不佳时已通过多轮继续）
    return {"supplements": supplements, "raw": "\n".join(raws)}


def pick_edge_timestamps(lines: list, duration: float = None) -> list:
    """按字幕时间轴取开头/中间/结尾三个抽查点（秒，去重）"""
    if not lines:
        if duration:
            return [1.0, float(duration) / 2, max(1.0, float(duration) - 1.0)]
        return [0.0]
    s0 = max(0.0, float(lines[0]["start"]))
    e_last = float(lines[-1]["end"])
    if duration:
        e_last = min(e_last, float(duration))
    if e_last - s0 >= 3.0:
        pts = [s0 + 0.5, (s0 + e_last) / 2, max(s0 + 0.5, e_last - 1.0)]
    else:
        pts = [s0 + 0.5, (s0 + e_last) / 2, max(s0 + 0.5, e_last - 0.5)]
    out = []
    for p in pts:
        p = round(max(0.0, p), 2)
        if not out or abs(p - out[-1]) > 0.5:
            out.append(p)
    return out


def extract_frames_at(video_path: str, timestamps: list) -> list:
    """对指定时间点各取最近一帧（seek 优化，长视频无需从头解码）→ [(秒, jpeg)]"""
    import av
    out = []
    for ts in sorted(set(round(float(t), 2) for t in timestamps)):
        container = av.open(video_path)
        try:
            stream = container.streams.video[0]
            tbase = float(stream.time_base)
            try:
                container.seek(int(ts / tbase), stream=stream, backward=True)
            except Exception:
                try:
                    container.seek(0)
                except Exception:
                    pass
            best = None
            for frame in container.decode(stream):
                fts = float(frame.pts * stream.time_base) if frame.pts is not None else ts
                if fts > ts + 0.05:
                    break
                best = frame
            if best is None:
                continue
            img = best.to_image().convert("RGB")
            if img.width > 640:  # 控体积：复检判断主题无需原始分辨率
                img = img.resize((640, max(1, int(img.height * 640 / img.width))))
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=70)
            out.append((int(round(ts)), buf.getvalue()))
        finally:
            container.close()
    return out


def _fmt_excerpt(lines: list, n: int = 3) -> str:
    """字幕首/中/尾各取 n 行（供归属复检对照）"""
    if not lines:
        return ""
    if len(lines) <= n * 3:
        sample = lines
    else:
        mid = len(lines) // 2
        sample = lines[:n] + lines[mid:mid + n] + lines[-n:]
    return "\n".join(f"[{int(l['start'])}s] {l['text']}" for l in sample)


def parse_match_verdict(text: str) -> bool:
    """解析归属判定：不一致→False；一致→True；其他→保守 False"""
    t = text or ""
    if "不一致" in t:
        return False
    return "一致" in t


def check_subtitle_match(cfg_vision: dict, video_path: str, lines: list,
                         duration: float = None) -> bool:
    """字幕归属复检：开头/中间/结尾 3 帧 + 字幕片段 → 多模态判定。
    返回 True=一致（可采信）/ False=不一致或无法判定（强制 Whisper）。
    API/解码异常向上抛，由调用方决定降级。"""
    if not lines:
        return False
    frames = extract_frames_at(video_path, pick_edge_timestamps(lines, duration))
    if not frames:
        raise VisionError("抽帧失败（无法解码或视频流不可用）")
    prompt = MATCH_PROMPT + "\n\n【待校验字幕片段（开头/中间/结尾）】\n" + _fmt_excerpt(lines)
    return parse_match_verdict(describe_video(cfg_vision, frames, prompt=prompt))
