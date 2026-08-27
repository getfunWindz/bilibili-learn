import sys, os, json, io
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import pytest
import vision

def _make_test_video(path, seconds=2, fps=10, size=(64, 64)):
    """用 PyAV 生成纯色测试视频"""
    import av
    container = av.open(path, mode="w")
    stream = container.add_stream("mpeg4", rate=fps)
    stream.width, stream.height = size
    stream.pix_fmt = "yuv420p"
    for i in range(seconds * fps):
        frame = av.VideoFrame(size[0], size[1], "rgb24")
        import numpy as np
        arr = np.full((size[1], size[0], 3), i * 10 % 255, dtype=np.uint8)
        frame = av.VideoFrame.from_ndarray(arr, format="rgb24")
        for packet in stream.encode(frame):
            container.mux(packet)
    for packet in stream.encode():
        container.mux(packet)
    container.close()
    return path

def test_extract_frames(tmp_path):
    v = _make_test_video(str(tmp_path / "t.mp4"), seconds=2, fps=10)
    frames = vision.extract_frames(v, interval_sec=1, max_frames=30)
    assert len(frames) >= 2  # 0s / 1s（2 秒视频抽到 2-3 帧）
    ts_list = [ts for ts, _ in frames]
    assert 0 in ts_list
    for ts, jpeg in frames:
        assert jpeg[:2] == b"\xff\xd8"  # JPEG 头

def test_extract_frames_respects_max(tmp_path):
    v = _make_test_video(str(tmp_path / "t.mp4"), seconds=2, fps=10)
    frames = vision.extract_frames(v, interval_sec=0.5, max_frames=2)
    assert len(frames) <= 2

def test_vision_not_configured():
    with pytest.raises(vision.VisionError):
        vision.describe_video({"enabled": False}, [])
    with pytest.raises(vision.VisionError):
        vision.describe_video({"enabled": True, "api_key": ""}, [])

def test_describe_video_payload(monkeypatch):
    captured = {}
    def fake_post(url, json=None, headers=None, timeout=None, trust_env=None):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        class R:
            def raise_for_status(self): pass
            def json(self):
                return {"choices": [{"message": {"content": "画面描述结果"}}]}
        return R()
    monkeypatch.setattr(vision.requests, "post", fake_post)
    cfg = {"enabled": True, "base_url": "https://api.example.com/v1",
           "api_key": "sk-test", "model": "qwen-vl-max"}
    frames = [(0, b"\xff\xd8fakejpeg"), (10, b"\xff\xd8fakejpeg2")]
    out = vision.describe_video(cfg, frames, prompt="测试提示")
    assert out == "画面描述结果"
    assert captured["url"] == "https://api.example.com/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer sk-test"
    msg = captured["json"]["messages"][0]["content"]
    kinds = [c["type"] for c in msg]
    assert kinds.count("image_url") == 2
    assert any("base64" in c["image_url"]["url"] for c in msg if c["type"] == "image_url")

def test_vision_transcribe(monkeypatch, tmp_path):
    monkeypatch.setattr(vision, "extract_frames",
                        lambda p, interval_sec=10, max_frames=24: [(0, b"jpeg")])
    monkeypatch.setattr(vision, "describe_video",
                        lambda cfg, frames, prompt=None: "[0] 画面文字内容")
    out = vision.vision_transcribe({"enabled": True}, "x.mp4")
    assert out == "[0] 画面文字内容"
