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

def test_find_content_gaps():
    """转写空档：相邻行间超过 min_gap 的时间区间"""
    lines = [{"start": 0, "end": 5, "text": "a"},
             {"start": 30, "end": 35, "text": "b"},
             {"start": 40, "end": 45, "text": "c"}]
    gaps = vision.find_content_gaps(lines, duration=60, min_gap=5.0)
    assert (5.0, 30.0) in gaps or any(abs(g[0] - 5) < 0.1 and abs(g[1] - 30) < 0.1 for g in gaps)
    # 末尾到视频结束的空档
    assert any(abs(g[0] - 45) < 0.1 and abs(g[1] - 60) < 0.1 for g in gaps)

def test_pick_check_intervals_empty_transcript():
    """转写为空 → 全视频作为复检区间"""
    intervals = vision.pick_check_intervals([], duration=120)
    assert intervals == [(0.0, 120.0)]

def test_pick_check_intervals_with_transcript():
    """转写有内容 → 复检空档区间"""
    lines = [{"start": 0, "end": 5, "text": "a"}, {"start": 60, "end": 65, "text": "b"}]
    intervals = vision.pick_check_intervals(lines, duration=70, min_gap=5.0)
    assert len(intervals) >= 1
    assert all(s < e for s, e in intervals)
    assert all(s >= 5.0 for s, _ in intervals[1:])  # 不含转写覆盖区

def test_extract_frames_range(tmp_path):
    """区间内每秒抽帧"""
    v = _make_test_video(str(tmp_path / "t.mp4"), seconds=4, fps=10)
    frames = vision.extract_frames_range(v, start_sec=1, end_sec=4, fps=1, max_frames=30)
    assert 2 <= len(frames) <= 4  # 1s/2s/3s（每秒 1 帧）
    assert frames[0][0] >= 1

def test_plan_check_intervals(monkeypatch):
    """模型规划复检区间：识别引导性字眼区域，区间不固定"""
    def fake_describe(cfg, frames, prompt=None):
        return "[120-140] 出现“根据这张表”引导语，可能有PPT要点\n[300-318] “看一下这段代码”，可能有代码演示"
    monkeypatch.setattr(vision, "describe_video", fake_describe)
    lines = [{"start": 0, "end": 5, "text": "a"}, {"start": 120, "end": 122, "text": "根据这张表"},
             {"start": 300, "end": 302, "text": "看一下这段代码"}]
    ranges = vision.plan_check_intervals({"enabled": True}, lines, duration=600, max_ranges=5)
    assert (120.0, 140.0) in [(s, e) for s, e, _r in ranges]
    assert (300.0, 318.0) in [(s, e) for s, e, _r in ranges]

def test_check_transcript_model_planned(monkeypatch):
    """复检使用模型规划的区间（引导性字眼区域），而非机械空档"""
    monkeypatch.setattr(vision, "plan_check_intervals",
                        lambda cfg, lines, duration, max_ranges=5, **k:
                            [(120.0, 140.0, "引导语")])
    monkeypatch.setattr(vision, "extract_frames_range", lambda *a, **k: [(120, b"jpeg")])
    monkeypatch.setattr(vision, "describe_video",
                        lambda cfg, frames, prompt=None: "[125] 白板：注意力公式")
    lines = [{"start": 120, "end": 122, "text": "根据这张表"}]
    result = vision.check_transcript({"enabled": True}, "x.mp4", lines, duration=600)
    assert any("注意力公式" in s["text"] for s in result["supplements"])

def test_check_transcript_multi_round(monkeypatch):
    """多轮复检：一轮效果不佳（有新发现）→ 继续多轮；无新发现则停止"""
    plan_calls = {"n": 0}
    def fake_plan(cfg, lines, duration, max_ranges=5, **k):
        plan_calls["n"] += 1
        if plan_calls["n"] == 1:
            return [(10.0, 30.0, "第一轮")]
        if plan_calls["n"] == 2:
            return [(50.0, 70.0, "第二轮")]
        return []
    monkeypatch.setattr(vision, "plan_check_intervals", fake_plan)
    monkeypatch.setattr(vision, "extract_frames_range", lambda *a, **k: [(10, b"jpeg")])
    desc_calls = {"n": 0}
    def fake_desc(cfg, frames, prompt=None):
        desc_calls["n"] += 1
        if desc_calls["n"] == 1:
            return "[12] 遗漏A"
        if desc_calls["n"] == 2:
            return "[55] 遗漏B"
        return "无遗漏"
    monkeypatch.setattr(vision, "describe_video", fake_desc)
    lines = [{"start": 0, "end": 5, "text": "a"}]
    result = vision.check_transcript({"enabled": True}, "x.mp4", lines, duration=100, max_rounds=3)
    texts = [s["text"] for s in result["supplements"]]
    assert any("遗漏A" in t for t in texts) and any("遗漏B" in t for t in texts)
    assert plan_calls["n"] >= 3  # 两轮有产出后仍问了第三轮（返回空才停）

def test_check_transcript_plan_fallback(monkeypatch):
    """模型规划失败 → 回退机械空档规则（健壮性）"""
    monkeypatch.setattr(vision, "plan_check_intervals",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("API 失败")))
    monkeypatch.setattr(vision, "extract_frames_range", lambda *a, **k: [(30, b"jpeg")])
    monkeypatch.setattr(vision, "describe_video", lambda cfg, frames, prompt=None: "[35] 遗漏C")
    lines = [{"start": 0, "end": 5, "text": "a"}, {"start": 40, "end": 45, "text": "b"}]
    result = vision.check_transcript({"enabled": True}, "x.mp4", lines, duration=50)
    assert any("遗漏C" in s["text"] for s in result["supplements"])

def test_check_transcript_flow(monkeypatch):
    """回退路径：模型规划失败 → 机械空档区间复检 → 有遗漏则补充"""
    monkeypatch.setattr(vision, "plan_check_intervals",
                        lambda *a, **k: (_ for _ in ()).throw(vision.VisionError("no plan")))
    calls = {"n": 0}
    def fake_describe(cfg, frames, prompt=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return "[8] 白板公式：E=mc²"
        return "无遗漏"
    monkeypatch.setattr(vision, "extract_frames_range", lambda *a, **k: [(8, b"jpeg")])
    monkeypatch.setattr(vision, "describe_video", fake_describe)
    lines = [{"start": 0, "end": 5, "text": "a"}, {"start": 30, "end": 35, "text": "b"}]
    result = vision.check_transcript({"enabled": True}, "x.mp4", lines, duration=40)
    assert any("白板公式" in s["text"] for s in result["supplements"])

