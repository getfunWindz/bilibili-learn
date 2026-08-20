import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import pytest
import transcriber

def test_load_whisper_not_installed(monkeypatch):
    monkeypatch.setitem(sys.modules, "faster_whisper", None)
    with pytest.raises(transcriber.WhisperNotInstalled):
        transcriber._load_whisper()

def test_detect_device_cpu_without_ctranslate2(monkeypatch):
    monkeypatch.setitem(sys.modules, "ctranslate2", None)
    assert transcriber.detect_device() == "cpu"

def test_download_audio(tmp_path, monkeypatch):
    calls = {}
    def fake_get(url, headers=None, stream=False, timeout=None):
        calls["url"] = url
        class R:
            def raise_for_status(self): pass
            def iter_content(self, chunk_size=1): yield b"1234"
        return R()
    monkeypatch.setattr(transcriber.requests, "get", fake_get)
    dest = transcriber.download_audio("http://x/audio.m4s", str(tmp_path / "a.m4s"))
    assert calls["url"] == "http://x/audio.m4s"
    assert open(dest, "rb").read() == b"1234"

def test_transcribe_gpu_failure_falls_back_cpu(monkeypatch):
    """检测到 CUDA 但库不可用（DLL 缺失）→ 自动回退 CPU 重试"""
    calls = []
    class FakeModel:
        def __init__(self, model_size, device, compute_type):
            calls.append((device, compute_type))
        def transcribe(self, audio_path, language="zh", vad_filter=False, progress_callback=None):
            if calls[-1][0] == "cuda":
                raise RuntimeError("Library cublas64_12.dll is not found")
            class S: pass
            s = S(); s.start, s.end, s.text = 0.0, 1.0, "ok"
            return [s], None
    monkeypatch.setattr(transcriber, "_load_whisper", lambda: FakeModel)
    monkeypatch.setattr(transcriber, "detect_device", lambda: "cuda")
    out = transcriber.transcribe("x.m4s")
    assert calls == [("cuda", "float16"), ("cpu", "int8")]  # 先 GPU，失败回退 CPU
    assert out == [{"start": 0.0, "end": 1.0, "text": "ok"}]

def test_transcribe_progress_callback(monkeypatch):
    seen = []
    class FakeModel:
        def __init__(self, *a, **k): pass
        def transcribe(self, audio_path, language="zh", vad_filter=False, progress_callback=None):
            if progress_callback:
                progress_callback(10, 100)
                progress_callback(100, 100)
            class S: pass
            s = S(); s.start, s.end, s.text = 0.0, 1.0, "hi"
            return [s], None
    monkeypatch.setattr(transcriber, "_load_whisper", lambda: FakeModel)
    transcriber.transcribe("x.m4s", progress_callback=lambda a, b: seen.append((a, b)))
    assert seen == [(10, 100), (100, 100)]

def test_clean_text_compresses_fillers():
    assert transcriber.clean_text("啊啊啊你好") == "啊你好"
    assert transcriber.clean_text("哈哈哈哈 对") == "哈哈 对"
    assert transcriber.clean_text("对对对 没错") == "对对 没错"
    assert transcriber.clean_text("这个  是   测试") == "这个 是 测试"
    assert transcriber.clean_text("正常句子") == "正常句子"  # 正常文本不变

def test_transcribe_applies_clean(monkeypatch):
    """转写输出经过 clean_text 清洗"""
    class FakeModel:
        def __init__(self, *a, **k): pass
        def transcribe(self, audio_path, language="zh", vad_filter=False, progress_callback=None):
            class S: pass
            s = S(); s.start, s.end, s.text = 0.0, 1.0, "啊啊啊  大家好"
            return [s], None
    monkeypatch.setattr(transcriber, "_load_whisper", lambda: FakeModel)
    out = transcriber.transcribe("x.m4s")
    assert out[0]["text"] == "啊 大家好"

def test_transcribe_vad_fallback(monkeypatch):
    """vad_filter 滤空时自动降级为 vad_filter=False 重试"""
    calls = []
    class FakeModel:
        def __init__(self, *a, **k): pass
        def transcribe(self, audio_path, language="zh", vad_filter=False, progress_callback=None):
            calls.append(vad_filter)
            if vad_filter:
                return [], None
            class S: pass
            s = S(); s.start, s.end, s.text = 0.0, 1.0, "hello"
            return [s], None
    monkeypatch.setattr(transcriber, "_load_whisper", lambda: FakeModel)
    out = transcriber.transcribe("x.m4s")
    assert calls == [True, False]  # 先 VAD，空则降级重试
    assert out == [{"start": 0.0, "end": 1.0, "text": "hello"}]

def test_transcribe_video_flow(tmp_path, monkeypatch):
    class FakeClient:
        def __init__(self): self.url_called = None
        def get_audio_url(self, bvid, cid): self.url_called = (bvid, cid); return "http://audio"
    client = FakeClient()
    monkeypatch.setattr(transcriber, "download_audio", lambda url, dest: dest)
    monkeypatch.setattr(transcriber, "transcribe",
                        lambda path, model_size="small", progress_callback=None: [{"start": 0.0, "end": 1.0, "text": "hi"}])
    out = transcriber.transcribe_video(client, "BV1GJ411x7h7", 1001)
    assert out == [{"start": 0.0, "end": 1.0, "text": "hi"}]
    assert client.url_called == ("BV1GJ411x7h7", 1001)
