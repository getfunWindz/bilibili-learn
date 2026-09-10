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


def check_transcript(cfg_vision: dict, video_path: str, lines: list, duration: float,
                     min_gap: float = 5.0, fps: int = 1, max_frames: int = 30) -> dict:
    """转写资料复检（固定路径）：对复检区间每秒抽帧，交多模态判断材料遗漏。
    返回 {supplements: [{start,end,text}], raw: str}"""
    intervals = pick_check_intervals(lines, duration, min_gap)
    supplements = []
    raws = []
    ctx = "\n".join(f"[{int(l['start'])}-{int(l['end'])}] {l['text']}" for l in (lines or [])[:200])
    for (s, e) in intervals:
        if e - s < 0.5:
            continue
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
    return {"supplements": supplements, "raw": "\n".join(raws)}
