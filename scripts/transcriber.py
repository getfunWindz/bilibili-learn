"""Whisper 兜底：下载音频 + faster-whisper 转写（CPU/GPU 自适应，无需系统 ffmpeg）"""
import os
import re
import sys
import tempfile
import requests

HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.bilibili.com"}

class WhisperNotInstalled(Exception):
    """faster-whisper 未安装"""

_FILLER_RULES = [
    (r"([啊哦嗯哎])\1{1,}", r"\1"),      # 语气单字：啊啊啊→啊（2+ 缩到 1）
    (r"(.)\1{2,}", r"\1\1"),            # 重复字：对对对→对对（3+ 缩到 2）
    (r"[ \t]{2,}", " "),                # 空白规范
]

def clean_text(text: str) -> str:
    """转写文本清洗（A3）：压缩语气词/重复字、规范空白。不改变时间戳与句意"""
    t = text.strip()
    for pat, rep in _FILLER_RULES:
        t = re.sub(pat, rep, t)
    return t.strip()

def _load_whisper():
    try:
        from faster_whisper import WhisperModel
        return WhisperModel
    except ImportError as e:
        raise WhisperNotInstalled(
            "未安装 faster-whisper，请执行: pip install faster-whisper"
        ) from e

def detect_device() -> str:
    """有 CUDA 用 cuda，否则 cpu（faster-whisper 基于 ctranslate2，无需 torch）"""
    try:
        import ctranslate2
        return "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
    except ImportError:
        return "cpu"

def download_audio(url: str, dest: str) -> str:
    r = requests.get(url, headers=HEADERS, stream=True, timeout=60)
    r.raise_for_status()
    with open(dest, "wb") as f:
        for chunk in r.iter_content(chunk_size=1 << 20):
            f.write(chunk)
    return dest

def transcribe(audio_path: str, model_size: str = "small", language: str = "zh",
                progress_callback=None) -> list:
    """转写音频 → [{start,end,text}]；faster-whisper 用 PyAV 解码，无需系统 ffmpeg。
    - GPU 检测误报/库缺失（cublas DLL 等）→ 自动回退 CPU int8
    - VAD 滤空（BGM 为主/低音量音频）→ 自动降级为不启用 VAD 重试
    - progress_callback(done_seconds, total_seconds) 可选进度回调"""
    WhisperModel = _load_whisper()
    device = detect_device()
    try:
        return _transcribe_with(WhisperModel, model_size, device, audio_path, language,
                                progress_callback)
    except RuntimeError as e:
        if device == "cpu":
            raise
        print(f"GPU 转写失败（{str(e)[:60]}），回退 CPU…", file=sys.stderr)
        return _transcribe_with(WhisperModel, model_size, "cpu", audio_path, language,
                                progress_callback)


def _transcribe_with(WhisperModel, model_size: str, device: str, audio_path: str,
                     language: str, progress_callback=None) -> list:
    compute = "int8" if device == "cpu" else "float16"
    model = WhisperModel(model_size, device=device, compute_type=compute)
    for vad in (True, False):
        segments, _ = model.transcribe(audio_path, language=language, vad_filter=vad,
                                       progress_callback=progress_callback)
        lines = [{"start": round(s.start, 2), "end": round(s.end, 2),
                  "text": clean_text(s.text)}  # A3：清洗语气词/重复
                 for s in segments]
        if lines:
            return lines
    return []

def transcribe_video(client, bvid: str, cid: int, model_size: str = "small",
                      progress_callback=None) -> list:
    """下载音频（临时目录）+ 转写，返回 [{start,end,text}]"""
    url = client.get_audio_url(bvid, cid)
    with tempfile.TemporaryDirectory() as td:
        audio_path = download_audio(url, os.path.join(td, "audio.m4s"))
        return transcribe(audio_path, model_size=model_size, progress_callback=progress_callback)
