import sys, os, json, argparse
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import pytest
import bili

VIEW = {"code": 0, "data": {
    "bvid": "BV1GJ411x7h7", "aid": 170001, "title": "测试视频",
    "owner": {"name": "测试UP"}, "duration": 300, "pubdate": 1600000000,
    "pages": [{"cid": 1001, "page": 1, "part": "P1 基础", "duration": 100}]}}

DEFAULT_LINES = [
    {"start": 0.0, "end": 2.5, "text": "大家好"}, {"start": 2.5, "end": 5.0, "text": "这是测试"}]

class FakeClient:
    def __init__(self, lines="default"):
        self.lines = DEFAULT_LINES if lines == "default" else lines
        self.search_out = []
    def get_video_info(self, bvid="", aid=0):
        assert bvid or aid
        from api_client import VideoInfo
        return VideoInfo(VIEW["data"])
    def get_subtitle_text(self, bvid, cid, duration=None, lang=None):
        if self.lines is None:
            return []
        from api_client import SubtitleLine
        return [SubtitleLine(l["start"], l["end"], l["text"]) for l in self.lines]
    def search(self, keyword, limit=5):
        return self.search_out

def report_dir(out):
    """cmd_run 输出在 out/<UP主>/<日期>_<标题>[/_P{n}] 子目录，取唯一路径"""
    ups = [d for d in os.listdir(out) if os.path.isdir(os.path.join(out, d))]
    assert len(ups) == 1, f"期望唯一 UP主 目录，实际 {ups}"
    sub = [d for d in os.listdir(os.path.join(out, ups[0]))
           if os.path.isdir(os.path.join(out, ups[0], d))]
    assert len(sub) == 1, f"期望唯一视频目录，实际 {sub}"
    return os.path.join(out, ups[0], sub[0])

def test_run_with_subtitle(tmp_path):
    out = tmp_path / "out"
    client = FakeClient()
    bili.cmd_run(argparse.Namespace(input="BV1GJ411x7h7", page=1, out=str(out),
                                    no_whisper=False, pick=1), client=client)
    rd = report_dir(out)
    files = sorted(os.listdir(rd))
    assert "subtitle.txt" in files and "video_info.json" in files and "report_template.md" in files
    info = json.load(open(os.path.join(rd, "video_info.json"), encoding="utf-8"))
    assert info["title"] == "测试视频" and info["page"] == 1 and info["subtitle_source"] == "字幕"
    sub = open(os.path.join(rd, "subtitle.txt"), encoding="utf-8").read()
    assert "大家好" in sub
    tpl = open(os.path.join(rd, "report_template.md"), encoding="utf-8").read()
    assert "## 分节详解" in tpl and "测试视频" in tpl
    assert "## 原话摘录" in tpl  # 学习报告要求：附上有参考价值的视频原话

def test_run_no_subtitle_no_whisper_flag(tmp_path):
    out = tmp_path / "out"
    client = FakeClient(lines=None)
    with pytest.raises(SystemExit) as e:
        bili.cmd_run(argparse.Namespace(input="BV1GJ411x7h7", page=1, out=str(out),
                                        no_whisper=True, pick=1), client=client)
    assert e.value.code == 3

def test_run_whisper_not_installed_guidance(tmp_path, monkeypatch):
    import transcriber
    def boom(*a, **k):
        raise transcriber.WhisperNotInstalled("未安装 faster-whisper，请执行: pip install faster-whisper")
    monkeypatch.setattr(transcriber, "transcribe_video", boom)
    out = tmp_path / "out"
    client = FakeClient(lines=None)
    with pytest.raises(SystemExit) as e:
        bili.cmd_run(argparse.Namespace(input="BV1GJ411x7h7", page=1, out=str(out),
                                        no_whisper=False, pick=1), client=client)
    assert e.value.code == 5

def test_run_keyword_search_pick(tmp_path):
    out = tmp_path / "out"
    client = FakeClient()
    client.search_out = [{"bvid": "BV1GJ411x7h7", "title": "测试视频", "author": "测试UP", "duration": "5:00"}]
    bili.cmd_run(argparse.Namespace(input="随便一个关键词", page=1, out=str(out),
                                    no_whisper=False, pick=1), client=client)
    rd = report_dir(out)
    info = json.load(open(os.path.join(rd, "video_info.json"), encoding="utf-8"))
    assert info["bvid"] == "BV1GJ411x7h7"

def test_resolve_cmd(capsys):
    bili.cmd_resolve(argparse.Namespace(input="https://www.bilibili.com/video/BV1GJ411x7h7/?p=2"))
    out = json.loads(capsys.readouterr().out)
    assert out["bvid"] == "BV1GJ411x7h7" and out["page"] == 2

VIEW3 = {"code": 0, "data": {
    "bvid": "BV1GJ411x7h7", "aid": 170001, "title": "测试合集",
    "owner": {"name": "测试UP"}, "duration": 300, "pubdate": 1600000000,
    "pages": [
        {"cid": 1001, "page": 1, "part": "P1 基础", "duration": 100},
        {"cid": 1002, "page": 2, "part": "P2 进阶", "duration": 100},
        {"cid": 1003, "page": 3, "part": "P3 实战", "duration": 100},
    ]}}

class MultiPageClient:
    """按 cid 返回不同 P 的字幕；lines_map: {cid: [SubtitleLine...] | None}"""
    def __init__(self, lines_map, view=VIEW3):
        self.lines_map = lines_map
        self.view = view
    def get_video_info(self, bvid="", aid=0):
        from api_client import VideoInfo
        return VideoInfo(self.view["data"])
    def get_subtitle_text(self, bvid, cid, duration=None, lang=None):
        from api_client import SubtitleLine
        raw = self.lines_map.get(cid)
        if raw is None:
            return []
        return [SubtitleLine(l["start"], l["end"], l["text"]) for l in raw]
    def search(self, keyword, limit=5):
        return []

def _mk_lines(texts):
    return [{"start": i * 1.0, "end": i * 1.0 + 0.8, "text": t} for i, t in enumerate(texts)]

def _batch_dir(out):
    ups = [d for d in os.listdir(out) if os.path.isdir(os.path.join(out, d))]
    assert len(ups) == 1, f"期望唯一 UP主 目录，实际 {ups}"
    d = [x for x in os.listdir(os.path.join(out, ups[0])) if x.endswith("_合集")]
    assert len(d) == 1, f"期望唯一 _合集 目录，实际 {d}"
    return os.path.join(out, ups[0], d[0])

def test_batch_all_structure(tmp_path):
    out = tmp_path / "out"
    client = MultiPageClient({
        1001: _mk_lines([f"第一课{i}" for i in range(12)]),
        1002: _mk_lines([f"第二课{i}" for i in range(12)]),
        1003: _mk_lines([f"第三课{i}" for i in range(12)]),
    })
    bili.cmd_run(argparse.Namespace(input="BV1GJ411x7h7", page=None, pages=None,
                                    all_=True, out=str(out), no_whisper=False, pick=1),
                 client=client)
    sub = _batch_dir(out)
    for p in ("P01", "P02", "P03"):
        assert os.path.isdir(os.path.join(sub, p)), f"缺少 {p}"
        assert os.path.exists(os.path.join(sub, p, "subtitle.txt"))
    info = json.load(open(os.path.join(sub, "video_info.json"), encoding="utf-8"))
    assert info["page_count"] == 3
    assert [r["status"] for r in info["pages"]] == ["ok", "ok", "ok"]
    assert info["pages"][0]["line_count"] == 12

def test_batch_partial_failure_continues(tmp_path):
    out = tmp_path / "out"
    client = MultiPageClient({
        1001: _mk_lines([f"第一课{i}" for i in range(12)]),
        1002: None,   # 无字幕且 --no-whisper → no_subtitle
        1003: _mk_lines([f"第三课{i}" for i in range(12)]),
    })
    bili.cmd_run(argparse.Namespace(input="BV1GJ411x7h7", page=None, pages=None,
                                    all_=True, out=str(out), no_whisper=True, pick=1),
                 client=client)
    sub = _batch_dir(out)
    info = json.load(open(os.path.join(sub, "video_info.json"), encoding="utf-8"))
    assert [r["status"] for r in info["pages"]] == ["ok", "no_subtitle", "ok"]

def test_batch_pages_range(tmp_path):
    out = tmp_path / "out"
    client = MultiPageClient({
        1001: _mk_lines([f"第一课{i}" for i in range(12)]),
        1002: _mk_lines([f"第二课{i}" for i in range(12)]),
        1003: _mk_lines([f"第三课{i}" for i in range(12)]),
    })
    bili.cmd_run(argparse.Namespace(input="BV1GJ411x7h7", page=None, pages="2-3",
                                    all_=False, out=str(out), no_whisper=False, pick=1),
                 client=client)
    sub = _batch_dir(out)
    assert os.path.isdir(os.path.join(sub, "P02"))
    assert os.path.isdir(os.path.join(sub, "P03"))
    assert not os.path.isdir(os.path.join(sub, "P01"))

def test_batch_failure_records_error(tmp_path):
    out = tmp_path / "out"
    client = MultiPageClient({
        1001: None,
        1002: _mk_lines([f"第二课{i}" for i in range(12)]),
    })
    bili.cmd_run(argparse.Namespace(input="BV1GJ411x7h7", page=None, pages="1-2",
                                    all_=False, out=str(out), no_whisper=True, pick=1),
                 client=client)
    sub = _batch_dir(out)
    info = json.load(open(os.path.join(sub, "video_info.json"), encoding="utf-8"))
    p1 = info["pages"][0]
    assert p1["status"] == "no_subtitle"
    assert "error" in p1 and p1["error"]  # 失败原因非空

def test_resume_skips_done_pages(tmp_path):
    out = tmp_path / "out"
    # 上次已成功 P1，这次只处理 P2、P3
    base = os.path.join(str(out), "测试UP", "2026-08-20_测试合集_合集")
    os.makedirs(base, exist_ok=True)
    json.dump({"title": "测试合集", "bvid": "BV1GJ411x7h7",
               "pages": [{"page": 1, "status": "ok"}, {"page": 2, "status": "failed"}]},
              open(os.path.join(base, "video_info.json"), "w", encoding="utf-8"))
    processed = []
    client = MultiPageClient({
        1001: _mk_lines([f"第一课{i}" for i in range(12)]),
        1002: _mk_lines([f"第二课{i}" for i in range(12)]),
        1003: _mk_lines([f"第三课{i}" for i in range(12)]),
    })
    orig = bili._process_page
    def spy(client_, info_, page_, root_, nw_, single_=False, lang=None, model_size=None):
        processed.append(page_)
        return orig(client_, info_, page_, root_, nw_, single_, lang, model_size)
    bili._process_page = spy
    try:
        bili.cmd_run(argparse.Namespace(input="BV1GJ411x7h7", page=None, pages=None,
                                        all_=True, out=str(out), no_whisper=False,
                                        pick=1, resume=True), client=client)
    finally:
        bili._process_page = orig
    assert processed == [2, 3]  # P1 已 ok 被跳过；P2 failed 重试

def test_batch_shows_progress_and_eta(tmp_path, capsys):
    out = tmp_path / "out"
    client = MultiPageClient({
        1001: _mk_lines([f"第一课{i}" for i in range(12)]),
        1002: _mk_lines([f"第二课{i}" for i in range(12)]),
        1003: _mk_lines([f"第三课{i}" for i in range(12)]),
    })
    bili.cmd_run(argparse.Namespace(input="BV1GJ411x7h7", page=None, pages=None,
                                    all_=True, out=str(out), no_whisper=False,
                                    pick=1, resume=False), client=client)
    out_text = capsys.readouterr().out
    assert "预计剩余" in out_text

def test_batch_generates_report_skeleton(tmp_path):
    out = tmp_path / "out"
    client = MultiPageClient({
        1001: _mk_lines([f"第一课{i}" for i in range(12)]),
        1002: _mk_lines([f"第二课{i}" for i in range(12)]),
    })
    bili.cmd_run(argparse.Namespace(input="BV1GJ411x7h7", page=None, pages=None,
                                    all_=True, out=str(out), no_whisper=False,
                                    pick=1, resume=False), client=client)
    sub = _batch_dir(out)
    tpl = open(os.path.join(sub, "report_template.md"), encoding="utf-8").read()
    assert "## P1" in tpl and "## P2" in tpl        # 按 P 分章
    assert "P1 基础" in tpl and "P2 进阶" in tpl      # 引用分P标题

def test_report_command_regenerates_single(tmp_path):
    out = tmp_path / "out"
    client = FakeClient()
    bili.cmd_run(argparse.Namespace(input="BV1GJ411x7h7", page=1, out=str(out),
                                    no_whisper=False, pick=1), client=client)
    single = report_dir(out)
    os.remove(os.path.join(single, "report_template.md"))  # 模拟模板丢失
    bili.cmd_report(argparse.Namespace(target=single))
    assert os.path.exists(os.path.join(single, "report_template.md"))

def test_report_command_handles_batch_dir(tmp_path):
    out = tmp_path / "out"
    client = MultiPageClient({
        1001: _mk_lines([f"第一课{i}" for i in range(12)]),
        1002: _mk_lines([f"第二课{i}" for i in range(12)]),
    })
    bili.cmd_run(argparse.Namespace(input="BV1GJ411x7h7", page=None, pages=None,
                                    all_=True, out=str(out), no_whisper=False,
                                    pick=1, resume=False), client=client)
    sub = _batch_dir(out)
    bili.cmd_report(argparse.Namespace(target=sub))
    tpl = open(os.path.join(sub, "report_template.md"), encoding="utf-8").read()
    assert "## P1" in tpl

def test_run_log_file_created(tmp_path):
    import logging
    out = tmp_path / "out"
    client = FakeClient()
    bili.setup_logging(str(tmp_path / "logs"))
    try:
        bili.cmd_run(argparse.Namespace(input="BV1GJ411x7h7", page=1, out=str(out),
                                        no_whisper=False, pick=1), client=client)
    finally:
        logging.shutdown()
    logfile = tmp_path / "logs" / "bili.log"
    assert logfile.exists()
    content = open(logfile, encoding="utf-8").read()
    assert "BV1GJ411x7h7" in content

def test_batch_writes_progress_incrementally(tmp_path):
    """批量中断时汇总已含已完成 P（resume 依赖）"""
    out = tmp_path / "out"
    client = MultiPageClient({
        1001: _mk_lines([f"第一课{i}" for i in range(12)]),
        1002: _mk_lines([f"第二课{i}" for i in range(12)]),
        1003: _mk_lines([f"第三课{i}" for i in range(12)]),
    })
    orig = bili._process_page
    calls = {"n": 0}
    def boom(client_, info_, page_, root_, nw_, single_=False, lang=None, model_size=None):
        calls["n"] += 1
        if calls["n"] == 2:
            raise KeyboardInterrupt()  # 模拟中断
        return orig(client_, info_, page_, root_, nw_, single_, lang, model_size)
    bili._process_page = boom
    try:
        with pytest.raises(KeyboardInterrupt):
            bili.cmd_run(argparse.Namespace(input="BV1GJ411x7h7", page=None, pages=None,
                                            all_=True, out=str(out), no_whisper=False,
                                            pick=1, resume=False), client=client)
    finally:
        bili._process_page = orig
    sub = _batch_dir(out)
    info = json.load(open(os.path.join(sub, "video_info.json"), encoding="utf-8"))
    assert [r["status"] for r in info["pages"]] == ["ok"]  # P1 已记录，P2 中断

def test_config_out_dir_used_when_no_out_arg(tmp_path, monkeypatch):
    import config as cfg
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"out_dir": str(tmp_path / "cfg_out")}), encoding="utf-8")
    monkeypatch.setattr(cfg, "CONFIG_PATH", str(p))
    client = FakeClient()
    bili.cmd_run(argparse.Namespace(input="BV1GJ411x7h7", page=1, out=None,
                                    no_whisper=False, pick=1), client=client)
    rd = report_dir(tmp_path / "cfg_out")
    assert os.path.exists(os.path.join(rd, "subtitle.txt"))

def test_output_archived_by_owner(tmp_path):
    """B3：输出按 UP 主归档为 <out>/<UP主>/<日期>_<标题>/"""
    out = tmp_path / "out"
    client = FakeClient()
    bili.cmd_run(argparse.Namespace(input="BV1GJ411x7h7", page=1, out=str(out),
                                    no_whisper=False, pick=1), client=client)
    up_dir = out / "测试UP"
    assert up_dir.is_dir()
    sub = [d for d in os.listdir(up_dir) if os.path.isdir(os.path.join(up_dir, d))][0]
    assert os.path.exists(up_dir / sub / "subtitle.txt")

def test_export_command(tmp_path):
    out = tmp_path / "out"
    client = FakeClient()
    bili.cmd_run(argparse.Namespace(input="BV1GJ411x7h7", page=1, out=str(out),
                                    no_whisper=False, pick=1), client=client)
    rd = report_dir(out)
    bili.cmd_export(argparse.Namespace(target=rd, format="html", out=None))
    assert os.path.exists(os.path.join(rd, "report_template.html"))
    bili.cmd_export(argparse.Namespace(target=rd, format="docx", out=None))
    assert os.path.exists(os.path.join(rd, "report_template.docx"))

def test_model_param_priority_over_config(tmp_path, monkeypatch):
    """--model CLI 参数优先于 config.whisper_model"""
    import config as cfg
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"whisper_model": "medium"}), encoding="utf-8")
    monkeypatch.setattr(cfg, "CONFIG_PATH", str(p))
    import transcriber
    seen = []
    orig = transcriber.transcribe_video
    def spy(client, bvid, cid, model_size="small", progress_callback=None):
        seen.append(model_size)
        return [{"start": 0.0, "end": 1.0, "text": "x"}]
    monkeypatch.setattr(transcriber, "transcribe_video", spy)
    out = tmp_path / "out"
    client = FakeClient(lines=None)  # 无字幕 → 走 whisper
    bili.cmd_run(argparse.Namespace(input="BV1GJ411x7h7", page=1, out=str(out),
                                    no_whisper=False, pick=1, lang=None,
                                    model="small"), client=client)
    assert seen == ["small"]  # CLI 覆盖 config 的 medium

def test_invalid_pages_graceful(tmp_path, capsys):
    """非法页码范围应友好报错退出，而非 Traceback 崩溃"""
    client = MultiPageClient({})
    with pytest.raises(SystemExit) as e:
        bili.cmd_run(argparse.Namespace(input="BV1GJ411x7h7", page=None, pages="5-2",
                                        all_=False, out=str(tmp_path / "o"), no_whisper=True,
                                        pick=1, resume=False, lang=None, model=None),
                     client=client)
    assert e.value.code == 2
    err = capsys.readouterr().err
    assert "无效" in err and "Traceback" not in err

def test_page_and_pages_mutually_exclusive():
    with pytest.raises(SystemExit):
        bili.main(["run", "BV1GJ411x7h7", "--page", "2", "--all"])
