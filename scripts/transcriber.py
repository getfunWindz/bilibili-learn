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

def _ensure_nvidia_paths() -> None:
    """C2: 自动探测 site-packages 下 nvidia 运行库（cuBLAS/cuDNN/nvrtc）并注入 PATH。
    pip 安装的 nvidia-cublas-cu12 / nvidia-cudnn-cu12 将 DLL 放在 site-packages/nvidia/*/bin，
    ctranslate2 按 PATH 查找，不注入则 GPU 转写报 cublas64_12.dll not found。"""
    import site as _site
    candidates = ["nvidia/cublas/bin", "nvidia/cudnn/bin", "nvidia/cuda_nvrtc/bin"]
    for sp in _site.getsitepackages():
        for sub in candidates:
            p = os.path.join(sp, sub.replace("/", os.sep))
            if os.path.isdir(p) and p not in os.environ.get("PATH", ""):
                os.environ["PATH"] = p + os.pathsep + os.environ["PATH"]


def detect_device() -> str:
    """有 CUDA 用 cuda，否则 cpu（faster-whisper 基于 ctranslate2，无需 torch）"""
    _ensure_nvidia_paths()
    try:
        import ctranslate2
        return "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
    except ImportError:
        return "cpu"

VAD_LONG_SEC_DEFAULT = 1800.0

def _probe_duration(audio_path: str) -> float:
    """尽力探测音频时长（秒）；失败返回 0（按短视频策略处理）"""
    try:
        import av
        container = av.open(audio_path)
        try:
            if container.duration:
                return float(container.duration) / 1_000_000  # av.time_base = 1e6
            st = container.streams.audio[0]
            if st.duration and st.time_base:
                return float(st.duration * st.time_base)
            return 0.0
        finally:
            container.close()
    except Exception:
        return 0.0

def _vad_plan(vad, duration_sec, vad_long_sec: float = VAD_LONG_SEC_DEFAULT) -> list:
    """VAD 尝试顺序：
    - 显式指定 → 单次；长视频默认先关（防长音频 VAD 切碎/漏内容，可 --vad 手动开）；
    - 短视频保持“先开、滤空则关”的原有兜底"""
    if vad is not None:
        return [bool(vad)]
    if duration_sec and float(duration_sec) >= float(vad_long_sec):
        return [False]
    return [True, False]

def download_audio(url: str, dest: str) -> str:
    r = requests.get(url, headers=HEADERS, stream=True, timeout=60)
    r.raise_for_status()
    with open(dest, "wb") as f:
        for chunk in r.iter_content(chunk_size=1 << 20):
            f.write(chunk)
    return dest

def transcribe(audio_path: str, model_size: str = "medium", language: str = "zh",
                progress_callback=None, vad=None, duration_sec: float = None,
                vad_long_sec: float = VAD_LONG_SEC_DEFAULT) -> list:
    """转写音频 → [{start,end,text}]；faster-whisper 用 PyAV 解码，无需系统 ffmpeg。
    - GPU 检测误报/库缺失（cublas DLL 等）→ 自动回退 CPU int8
    - VAD：显式 vad=True/False 强制；默认（auto）长视频禁用、短视频先开滤空则关
    - progress_callback(done_seconds, total_seconds) 可选进度回调"""
    WhisperModel = _load_whisper()
    device = detect_device()
    duration = duration_sec if duration_sec else _probe_duration(audio_path)
    plan = _vad_plan(vad, duration, vad_long_sec)
    try:
        return _transcribe_with(WhisperModel, model_size, device, audio_path, language,
                                progress_callback, plan)
    except RuntimeError as e:
        if device == "cpu":
            raise
        print(f"GPU 转写失败（{str(e)[:60]}），回退 CPU…", file=sys.stderr)
        return _transcribe_with(WhisperModel, model_size, "cpu", audio_path, language,
                                progress_callback, plan)


def _transcribe_with(WhisperModel, model_size: str, device: str, audio_path: str,
                     language: str, progress_callback=None,
                     vad_plan: tuple = (True, False)) -> list:
    compute = "int8" if device == "cpu" else "float16"
    model = WhisperModel(model_size, device=device, compute_type=compute)
    for vad in vad_plan:
        # faster-whisper >=1.2 移除 progress_callback；旧版/测试的 FakeModel 仍用它
        try:
            segments, _ = model.transcribe(audio_path, language=language, vad_filter=vad,
                                           progress_callback=progress_callback)
        except TypeError:
            segments, _ = model.transcribe(audio_path, language=language, vad_filter=vad,
                                           log_progress=progress_callback is not None)
        lines = [{"start": round(s.start, 2), "end": round(s.end, 2),
                  "text": clean_text(s.text)}  # A3：清洗语气词/重复
                 for s in segments]
        if lines:
            return lines
    return []

def transcribe_video(client, bvid: str, cid: int, model_size: str = "medium",
                      progress_callback=None, vad=None, duration_sec: float = None,
                      vad_long_sec: float = VAD_LONG_SEC_DEFAULT) -> list:
    """下载音频（临时目录）+ 转写，返回 [{start,end,text}]；vad/duration 透传（长视频默认禁 VAD）"""
    url = client.get_audio_url(bvid, cid)
    with tempfile.TemporaryDirectory() as td:
        audio_path = download_audio(url, os.path.join(td, "audio.m4s"))
        return transcribe(audio_path, model_size=model_size, progress_callback=progress_callback,
                          vad=vad, duration_sec=duration_sec, vad_long_sec=vad_long_sec)


def transcribe_video_with_progress(client, bvid: str, cid: int, model_size: str = "medium",
                                   progress_callback=None, **kwargs) -> list:
    """带进度回调的转写（供 bili.py 使用；内部映射为 log_progress）"""
    return transcribe_video(client, bvid, cid, model_size=model_size,
                            progress_callback=progress_callback, **kwargs)
