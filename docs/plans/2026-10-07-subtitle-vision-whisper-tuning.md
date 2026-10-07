# 字幕多模态复检 + Whisper/限流调优 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use `executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复"字幕校验过严误杀"（引入多模态 3 帧归属复检 + 错乱字幕强制 Whisper），优化接口限流重试，调整 Whisper 默认策略（长视频禁用 VAD、默认 medium）。

**Architecture:**
- 字幕校验从"硬性二元判定"升级为**分级**：`ok / suspect / invalid`（`api_client.SubtitleResult`）。软可疑（suspect）交多模态复检裁决：一致→采信，不一致→强制 Whisper；硬不可信（invalid，如时长越界 >130%）直接 Whisper。
- 多模态复检只抽 **3 帧**（字幕时间轴开头/中间/结尾），1 次 API 调用/视频，画面流用**最低码率**下载控成本；默认仅在防线判可疑时触发（`vision.subtitle_check: "auto"`，可选 `always/off`）。
- API 层引入**有界指数退避重试**（412/429/5xx/超时/风控 HTML），412 时刷新 buvid3 cookie；字幕文件裸 JSON 下载同样走重试。
- Whisper：`whisper_model` 默认 `medium`；`whisper_vad: auto`（≥ `vad_long_sec`=1800s 的长视频默认不启用 VAD，可 `--vad` 手动打开）。

**Tech Stack:** Python 3.10 · pytest · PyAV · requests · faster-whisper（可选）

---

## 背景与前置状态（本 session 已完成）

- 本地 main 已快进同步 `origin/main`（`46ccfe9`，含"多模态复检固定路径/R12/frames 命令"5 个远程提交）。
- 工作区遗留的重复代码改动（与远程一致）已丢弃；**过期的 `scripts/glossary.py` 改动已备份并丢弃**（它删除了 `bilibili-to-wiki` 技能依赖的 alias/clean/absorb 命令）。
- `references/glossary.json` 本机新增 59 行术语/勘误数据**已保留**（未提交，待用户确认是否提交）。
- 备份位置：`C:\Users\getfunWindz\AppData\Local\Temp\bilibili-learn-wip-20261007\`（含 `glossary.py.worktree`、`glossary.json.worktree`、`wip.patch`）。
- 基线：`python -m pytest tests/ -q` → **121 passed**。

## 待用户确认的决策点

| # | 决策 | 提案默认 |
|---|------|---------|
| D1 | "长视频"阈值 | 30 分钟（`vad_long_sec: 1800`，可配） |
| D2 | 长视频 VAD 关闭后若转写为空 | 不回退开启 VAD（避免 2 小时视频二次全量转写）；用户可 `--vad` 重跑 |
| D3 | 多模态复检触发时机 | ✅ 双模式确认：默认 `auto`（防线初筛）；可选 `always`（全量把关）/`off`（关闭）。**配合放宽字幕防线阈值**（见 Task 1：覆盖率 60%→50%、关键词零命中判定需 ≥4 词）；并在配置后告知用户可自选模式，**以用户选择为准** |
| D4 | 重试参数 | ✅ 默认 **4 次**重试（原提案 3 次 +1），退避基数 1.5s（指数 ×2 + 抖动），可配 `api_retries` / `api_retry_base` |

---

## Task 1: 字幕校验分级（`SubtitleResult`）

**Files:**
- Modify: `scripts/api_client.py`
- Test: `tests/test_api_client.py`、`tests/test_subtitle_quality.py`

- [ ] **Step 1: 写失败测试（分级判定 + 候选保留 + 兼容包装）**

在 `tests/test_subtitle_quality.py` 追加：

```python
# ---------- 分级判定（suspect/invalid） ----------

def test_verdict_normal_ok():
    lines = _lines(NORMAL_TEXTS)
    status, reason = ApiClient._subtitle_verdict(lines, TITLE_KV, "", 800)
    assert status == "ok" and reason == ""

def test_verdict_overrun_invalid():
    from api_client import SubtitleLine as SL
    lines = _lines(NORMAL_TEXTS) + [SL(0.0, 10109.0, "越界")]
    status, reason = ApiClient._subtitle_verdict(lines, TITLE_KV, "", 615)
    assert status == "invalid" and "越界" in reason

def test_verdict_keyword_zero_hit_suspect():
    lines = _lines(RANGE_TEXTS)
    status, reason = ApiClient._subtitle_verdict(lines, TITLE_KV, "", 500)
    assert status == "suspect" and "零命中" in reason

def test_verdict_short_coverage_suspect():
    from api_client import SubtitleLine as SL
    lines = [SL(i * 1.0, i * 1.0 + 0.9, f"第{i}句") for i in range(12)]  # 覆盖 12s
    status, reason = ApiClient._subtitle_verdict(lines, "", "", 900)
    assert status == "suspect" and "覆盖不足" in reason

# ---------- 放宽阈值（D3 决策：防线已多次被判过严，略微放宽） ----------

def test_verdict_relaxed_coverage_50pct():
    """覆盖 53%（原 60% 会误杀）→ 判 ok"""
    lines = _lines(NORMAL_TEXTS[:12])  # 12 行 × 2s = 24s，末条 24s
    status, reason = ApiClient._subtitle_verdict(lines, TITLE_KV, "", 45)
    assert status == "ok", f"放宽后仍误杀：{status}/{reason}"

def test_verdict_relaxed_keyword_few_words():
    """标题关键词不足 4 个（原 ≥2 词就查）→ 不做零命中判定，不误杀"""
    lines = _lines(RANGE_TEXTS)
    status, reason = ApiClient._subtitle_verdict(lines, "随便聊聊", "", 900)
    assert status == "ok", f"少关键词标题被误杀：{status}/{reason}"

def test_verdict_keyword_enough_words_still_flagged():
    """关键词 ≥4 词且零命中 → 仍判可疑（错乱防线保留）"""
    lines = _lines(RANGE_TEXTS)
    status, reason = ApiClient._subtitle_verdict(lines, TITLE_KV, "", 500)
    assert status == "suspect" and "零命中" in reason
```

在 `tests/test_api_client.py` 追加：

```python
def test_fetch_subtitle_suspect_keeps_candidate():
    """可疑字幕不再直接丢弃：返回 suspect + 候选行（供多模态复检）"""
    subs = [{"lan": "ai-zh", "lan_doc": "中文", "subtitle_url": "//s1.hdslb.com/x.json"}]
    body = [{"from": i * 1.0, "to": i * 1.0 + 0.9, "content": f"词{i}"} for i in range(12)]
    client = make_client(subtitles=subs, sub_body=body)
    res = client.fetch_subtitle("BV1GJ411x7h7", 1001, duration=900, title="KV Cache 原理")
    assert res.status == "suspect"
    assert len(res.lines) == 12 and res.reason

def test_fetch_subtitle_ok():
    subs = [{"lan": "ai-zh", "lan_doc": "中文", "subtitle_url": "//s1.hdslb.com/x.json"}]
    body = [{"from": i * 1.0, "to": i * 1.0 + 0.9, "content": f"稳定{i}"} for i in range(12)]
    client = make_client(subtitles=subs, sub_body=body)
    res = client.fetch_subtitle("BV1GJ411x7h7", 1001)
    assert res.status == "ok" and len(res.lines) == 12

def test_fetch_subtitle_invalid_overrun():
    subs = [{"lan": "ai-zh", "lan_doc": "中文", "subtitle_url": "//s1.hdslb.com/x.json"}]
    body = [{"from": i * 10.0, "to": i * 10.0 + 9.0, "content": f"错位{i}"} for i in range(12)]
    client = make_client(subtitles=subs, sub_body=body)
    res = client.fetch_subtitle("BV1GJ411x7h7", 1001, duration=60)
    assert res.status == "invalid"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_subtitle_quality.py tests/test_api_client.py -q`
Expected: FAIL（`_subtitle_verdict`/`fetch_subtitle` 不存在）

- [ ] **Step 3: 实现分级与 `fetch_subtitle`**

`scripts/api_client.py` 顶部 `import time` 附近加 `import random`，新增类：

```python
class SubtitleResult:
    """字幕抓取分级结果：ok=可信 / suspect=软可疑（可多模态复检拯救）/ invalid=硬不可信 / empty=无字幕"""
    def __init__(self, lines: list, status: str, reason: str = ""):
        self.lines, self.status, self.reason = lines, status, reason
```

`ApiClient.get_subtitle_text` 改为兼容包装 + 新增 `fetch_subtitle`/`_subtitle_verdict`：

```python
    @staticmethod
    def _subtitle_verdict(lines: list, title: str = "", desc: str = "",
                          duration: int = None) -> tuple:
        """字幕分级：返回 (status, reason)
        - invalid：时长越界（末条 > 130% 时长，典型"拉取了别的视频字幕"）→ 强制 Whisper
        - suspect：覆盖不足/行数偏少/歌词/标题关键词零命中/行密度异常 → 可多模态复检
        - ok：结构完整且内容合理
        阈值放宽（D3 决策，v3.4）：覆盖下限 60%→50%；标题关键词零命中判定需 ≥4 词。
        """
        if not lines:
            return "empty", "无字幕"
        if duration:
            last = lines[-1].end
            if last > duration * COVERAGE_MAX_RATIO:
                return "invalid", f"时长越界({last:.0f}s>{duration * COVERAGE_MAX_RATIO:.0f}s)"
            if last < duration * COVERAGE_MIN_RATIO or len(lines) < MIN_SUBTITLE_LINES:
                return "suspect", f"覆盖不足({len(lines)}行 末条{last:.0f}s)"
        elif len(lines) < MIN_SUBTITLE_LINES:
            return "suspect", f"行数偏少({len(lines)}行)"
        plausible, reason = ApiClient._subtitle_plausible(lines, title, desc, duration)
        if not plausible:
            return "suspect", reason
        return "ok", ""

（同时在模块顶部新增放宽常量，并在 `_subtitle_complete` / `_subtitle_plausible` 中复用：）

```python
COVERAGE_MIN_RATIO = 0.5    # v3.4 放宽（60%→50%）：末条 ≥ 50% 时长即可
COVERAGE_MAX_RATIO = 1.3
MIN_SUBTITLE_LINES = 10
KEYWORD_MIN_COUNT = 4       # v3.4 放宽（2→4）：标题关键词少于 4 个不做零命中判定
```

- `_subtitle_complete`：`last < duration * 0.6` → `last < duration * COVERAGE_MIN_RATIO`；`last > duration * 1.3` → `COVERAGE_MAX_RATIO`；`len(lines) < 10` → `MIN_SUBTITLE_LINES`。
- `_subtitle_plausible`：`if len(kws) >= 2:` → `if len(kws) >= KEYWORD_MIN_COUNT:`（reason 文案不变）。

    def fetch_subtitle(self, bvid: str, cid: int, duration: int = None, lang: str = None,
                       title: str = "", desc: str = "") -> "SubtitleResult":
        """抓取字幕并分级（最多两次抓取）：
        - 无字幕 → empty（不再重抓，省请求）
        - 越界 → invalid（硬不可信，不再重抓）
        - 首抓 ok → 二次确认：两次一致 → ok；不一致 → suspect（保留候选）
        - 首抓 suspect → 二次抓取：二次 ok → 仍标 suspect（候选用二次的，交多模态裁决）
        - 两次均 suspect → suspect（候选取更长者）"""
        last = None
        for attempt in range(2):
            lines = self._fetch_subtitle_once(bvid, cid, lang)
            if not lines:
                return SubtitleResult([], "empty", "无字幕")
            status, reason = self._subtitle_verdict(lines, title, desc, duration)
            if status == "invalid":
                return SubtitleResult(lines, "invalid", reason)
            if attempt == 0:
                last = SubtitleResult(lines, status, reason)
                time.sleep(random.uniform(0.8, 1.5))  # 抖动间隔，拿独立的第二次抓取
                continue
            # 第二次抓取
            if status == "ok":
                if last.status == "ok" and lines == last.lines:
                    return SubtitleResult(lines, "ok", "")
                if last.status == "ok":
                    return SubtitleResult(lines, "suspect", "两次获取内容不一致")
                return SubtitleResult(lines, "suspect", f"首次可疑({last.reason})")
            cand = lines if len(lines) >= len(last.lines) else last.lines
            return SubtitleResult(cand, "suspect", reason or last.reason)
        return last or SubtitleResult([], "empty", "无字幕")

    def get_subtitle_text(self, bvid: str, cid: int, duration: int = None, attempts: int = 2,
                          lang: str = None, title: str = "", desc: str = "") -> list:
        """兼容包装：仅返回可信字幕（ok），其余返回 [] 由上层降级/复检"""
        res = self.fetch_subtitle(bvid, cid, duration=duration, lang=lang,
                                  title=title, desc=desc)
        return res.lines if res.status == "ok" else []
```

（删除原 `get_subtitle_text` 的两次循环实现；`attempts` 参数保留兼容签名。）

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_subtitle_quality.py tests/test_api_client.py -q`
Expected: PASS（含既有 `test_subtitle_*` 全部语义保持）

- [ ] **Step 5: 提交**

```bash
git add scripts/api_client.py tests/test_api_client.py tests/test_subtitle_quality.py
git commit -m "feat(api): 字幕校验分级（ok/suspect/invalid）并保留可疑候选供多模态复检"
```

---

## Task 2: 多模态字幕归属复检（vision）

**Files:**
- Modify: `scripts/vision.py`
- Test: `tests/test_vision.py`

- [ ] **Step 1: 写失败测试**

`tests/test_vision.py` 追加：

```python
def test_pick_edge_timestamps():
    lines = [{"start": 10, "end": 12, "text": "a"}, {"start": 90, "end": 98, "text": "b"}]
    tss = vision.pick_edge_timestamps(lines, duration=100)
    assert len(tss) == 3
    assert tss[0] == 10.5 and tss[1] == 54.0 and 97 <= tss[2] <= 100

def test_pick_edge_timestamps_short_clip():
    lines = [{"start": 0, "end": 1.5, "text": "a"}]
    tss = vision.pick_edge_timestamps(lines, duration=2)
    assert tss and all(0 <= t <= 2 for t in tss)

def test_extract_frames_at(tmp_path):
    v = _make_test_video(str(tmp_path / "t.mp4"), seconds=4, fps=10)
    frames = vision.extract_frames_at(v, [0.5, 2.0, 3.5])
    assert len(frames) >= 2
    for ts, jpeg in frames:
        assert jpeg[:2] == b"\xff\xd8"

def test_parse_match_verdict():
    assert vision.parse_match_verdict("一致") is True
    assert vision.parse_match_verdict("不一致：这是影视剧台词") is False
    assert vision.parse_match_verdict("无法判断") is False   # 未知输出 → 保守

def test_check_subtitle_match(monkeypatch):
    monkeypatch.setattr(vision, "extract_frames_at",
                        lambda p, tss: [(0, b"j"), (50, b"j"), (99, b"j")])
    captured = {}
    def fake_desc(cfg, frames, prompt=None):
        captured["prompt"] = prompt
        return "一致"
    monkeypatch.setattr(vision, "describe_video", fake_desc)
    lines = [{"start": i * 10, "end": i * 10 + 8, "text": f"第{i}句 KV Cache 讲解"} for i in range(10)]
    assert vision.check_subtitle_match({"enabled": True}, "x.mp4", lines, duration=100) is True
    assert "KV Cache" in captured["prompt"]

def test_check_subtitle_match_mismatch(monkeypatch):
    monkeypatch.setattr(vision, "extract_frames_at", lambda p, tss: [(0, b"j")])
    monkeypatch.setattr(vision, "describe_video",
                        lambda cfg, frames, prompt=None: "不一致：内容为影视剧台词")
    lines = [{"start": 0, "end": 8, "text": "你还是人吗我的好夫人"} for _ in range(10)]
    assert vision.check_subtitle_match({"enabled": True}, "x.mp4", lines, duration=100) is False
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_vision.py -q`
Expected: FAIL（函数不存在）

- [ ] **Step 3: 实现**

`scripts/vision.py` 在 `PLAN_PROMPT` 后追加 `MATCH_PROMPT`，在文件尾部追加函数：

```python
MATCH_PROMPT = (
    "你是学习报告工具的「字幕归属校验」助手。以下是同一个视频在开头/中间/结尾三个时间点的画面帧，"
    "以及待校验字幕在开头/中间/结尾的片段。\n"
    "请判断这些字幕是否属于这个视频：\n"
    "- 主题/领域是否一致（画面中的板书、PPT 标题、演示内容与字幕是否相关）\n"
    "- 字幕是否明显来自其他视频（如影视剧台词、歌词、其他领域的教程、完全无关的口播）\n"
    "只输出一行结论：`一致` 或 `不一致：简短理由`。不要输出其他内容。"
)

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
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_vision.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add scripts/vision.py tests/test_vision.py
git commit -m "feat(vision): 字幕归属复检（首/中/尾 3 帧定点抽帧 + 一致/不一致判定）"
```

---

## Task 3: 接线到 `_process_page`（可疑字幕 → 复检 → 强制 Whisper）

**Files:**
- Modify: `scripts/bili.py`、`scripts/api_client.py`（`get_video_url(lowest=)`）
- Test: `tests/test_cli.py`

- [ ] **Step 1: 写失败测试**

`tests/test_cli.py` 中给 `FakeClient`/`MultiPageClient` 增加：

```python
    def fetch_subtitle(self, bvid, cid, duration=None, lang=None, title="", desc=""):
        from api_client import SubtitleResult, SubtitleLine
        if self.lines is None:
            return SubtitleResult([], "empty", "无字幕")
        return SubtitleResult(
            [SubtitleLine(l["start"], l["end"], l["text"]) for l in self.lines], "ok", "")
```

并追加测试：

```python
class SuspectClient(FakeClient):
    """返回可疑字幕的假客户端（用于复检接线测试）"""
    def fetch_subtitle(self, bvid, cid, duration=None, lang=None, title="", desc=""):
        from api_client import SubtitleResult, SubtitleLine
        return SubtitleResult(
            [SubtitleLine(l["start"], l["end"], l["text"]) for l in self.lines],
            "suspect", "标题关键词零命中")

class InvalidClient(SuspectClient):
    def fetch_subtitle(self, bvid, cid, duration=None, lang=None, title="", desc=""):
        from api_client import SubtitleResult
        return SubtitleResult([], "invalid", "时长越界")


def test_suspect_subtitle_rescued_by_vision(tmp_path, monkeypatch):
    """可疑字幕 + 多模态复检通过 → 采信字幕，不调用 Whisper"""
    import transcriber as tr_mod
    monkeypatch.setattr(tr_mod, "transcribe_video",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("不应调用 Whisper")))
    monkeypatch.setattr(bili, "_vision_verify_subtitle", lambda *a, **k: True)
    monkeypatch.setattr(bili, "_vision_check", lambda *a, **k: [])
    monkeypatch.setattr(bili.config, "load_config",
                        lambda: {"vision": {"enabled": True, "api_key": "k"}})
    out = tmp_path / "out"
    bili.cmd_run(argparse.Namespace(input="BV1GJ411x7h7", page=1, out=str(out),
                                    no_whisper=False, pick=1, lang=None, model=None),
                 client=SuspectClient())
    rd = report_dir(out)
    info = json.load(open(os.path.join(rd, "video_info.json"), encoding="utf-8"))
    assert info["subtitle_source"] == "字幕+vision复核"

def test_suspect_subtitle_mismatch_forces_whisper(tmp_path, monkeypatch):
    """复检判定不一致 → 强制 Whisper（错乱字幕场景）"""
    import transcriber as tr_mod
    monkeypatch.setattr(tr_mod, "transcribe_video",
                        lambda *a, **k: [{"start": 0.0, "end": 1.0, "text": "whisper 转写"}])
    monkeypatch.setattr(bili, "_vision_verify_subtitle", lambda *a, **k: False)
    monkeypatch.setattr(bili, "_vision_check", lambda *a, **k: [])
    monkeypatch.setattr(bili.config, "load_config",
                        lambda: {"vision": {"enabled": True, "api_key": "k"}})
    out = tmp_path / "out"
    bili.cmd_run(argparse.Namespace(input="BV1GJ411x7h7", page=1, out=str(out),
                                    no_whisper=False, pick=1, lang=None, model=None),
                 client=SuspectClient())
    rd = report_dir(out)
    info = json.load(open(os.path.join(rd, "video_info.json"), encoding="utf-8"))
    assert info["subtitle_source"] == "whisper"

def test_invalid_subtitle_skips_vision_check(tmp_path, monkeypatch):
    """硬不可信（越界）→ 不调用多模态，直接 Whisper"""
    import transcriber as tr_mod
    called = {"n": 0}
    def spy(*a, **k):
        called["n"] += 1
        return True
    import transcriber
    monkeypatch.setattr(transcriber, "transcribe_video",
                        lambda *a, **k: [{"start": 0.0, "end": 1.0, "text": "w"}])
    monkeypatch.setattr(bili, "_vision_verify_subtitle", spy)
    monkeypatch.setattr(bili, "_vision_check", lambda *a, **k: [])
    monkeypatch.setattr(bili.config, "load_config",
                        lambda: {"vision": {"enabled": True, "api_key": "k"}})
    out = tmp_path / "out"
    bili.cmd_run(argparse.Namespace(input="BV1GJ411x7h7", page=1, out=str(out),
                                    no_whisper=False, pick=1, lang=None, model=None),
                 client=InvalidClient())
    assert called["n"] == 0
```

以及 `get_video_url` 低码率测试（`tests/test_api_client.py`）：

```python
def test_video_url_lowest_picks_min_bandwidth():
    def handler(path, params):
        if path == "/x/player/playurl":
            return {"code": 0, "data": {"dash": {"video": [
                {"bandwidth": 100, "baseUrl": "http://lowv"},
                {"bandwidth": 800, "baseUrl": "http://highv"}]}}}
        raise AssertionError(path)
    client = ApiClient(FakeSession(handler))
    assert client.get_video_url("BV1GJ411x7h7", 1001, lowest=True) == "http://lowv"
    assert client.get_video_url("BV1GJ411x7h7", 1001) == "http://highv"
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_cli.py -q`（新用例 FAIL）

- [ ] **Step 3: 实现**

`scripts/api_client.py`：

```python
    def get_video_url(self, bvid: str, cid: int, lowest: bool = False) -> str:
        """视频画面流直链；lowest=True 取最低码率（复检抽帧省带宽）"""
        data = self._get("/x/player/playurl", {"bvid": bvid, "cid": cid, "fnval": 16})
        videos = (data.get("dash") or {}).get("video") or []
        if not videos:
            raise BiliError(-404, "无法获取视频流（可能需要登录）")
        videos.sort(key=lambda v: v.get("bandwidth", 0), reverse=not lowest)
        return videos[0]["baseUrl"]
```

`scripts/bili.py` 新增两个函数（放在 `_vision_check` 附近）：

```python
def _want_subtitle_check(cfg_vision: dict, status: str) -> bool:
    """是否执行字幕归属复检：always/true 恒查；off/false 关闭；auto（默认）仅可疑字幕"""
    if not cfg_vision.get("enabled") or not cfg_vision.get("api_key"):
        return False
    mode = cfg_vision.get("subtitle_check", "auto")
    if mode in (False, "off", "false", None):
        return False
    if mode in (True, "always", "true"):
        return True
    return status == "suspect"

def _vision_verify_subtitle(client, bvid: str, cid: int, cfg_vision: dict, lines: list,
                            duration: float):
    """字幕归属复检：最低码率画面流 → 首/中/尾 3 帧 → 多模态判定。
    返回 True=一致（采信）/ False=不一致（强制 Whisper）/ None=未执行"""
    import tempfile
    import vision
    import transcriber as tr
    if not lines:
        return None
    print("  · 字幕可疑：多模态归属复检（开头/中间/结尾 3 帧）……", file=sys.stderr)
    url = client.get_video_url(bvid, cid, lowest=True)
    with tempfile.TemporaryDirectory() as td:
        vp = os.path.join(td, "video.m4s")
        tr.download_audio(url, vp)  # 复用流式下载（任意二进制）
        return vision.check_subtitle_match(cfg_vision, vp, lines, duration)
```

`_process_page` 中替换字幕获取段（原 `try: lines = [...get_subtitle_text...]`）：

```python
    src = "字幕"
    try:
        res = client.fetch_subtitle(info.bvid, page_obj.cid, duration=page_obj.duration,
                                    lang=lang, title=info.title, desc=info.desc)
    except Exception as e:
        err = str(e)[:100]
        print(f"  P{page} 字幕获取失败：{err}", file=sys.stderr)
        return "failed", "", 0, "", err
    lines = [{"start": l.start, "end": l.end, "text": l.content} for l in res.lines]
    if res.status == "suspect":
        cfgv0 = config.load_config().get("vision", {})
        verdict = None
        if not no_vision_check and _want_subtitle_check(cfgv0, res.status):
            try:
                verdict = _vision_verify_subtitle(client, info.bvid, page_obj.cid, cfgv0,
                                                  lines, page_obj.duration)
            except Exception as e:
                print(f"  P{page} 字幕归属复检失败（保守降级）：{str(e)[:80]}", file=sys.stderr)
        if verdict is True:
            src = "字幕+vision复核"
            print(f"  P{page} 字幕归属复检通过（原判可疑：{res.reason}）", file=sys.stderr)
        else:
            why = "归属复检不一致" if verdict is False else f"可疑（{res.reason}）"
            print(f"  P{page} 字幕{why} → 强制 Whisper", file=sys.stderr)
            lines = []
    elif res.status == "invalid":
        print(f"  P{page} 字幕不可信（{res.reason}）→ 强制 Whisper", file=sys.stderr)
        lines = []
```

（其后的 `if not lines: ... Whisper ...` 与复检阶段逻辑保持不变。）

- [ ] **Step 4: 全量测试**

Run: `python -m pytest tests/ -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add scripts/bili.py scripts/api_client.py tests/test_cli.py tests/test_api_client.py
git commit -m "feat(bili): 可疑字幕接入多模态归属复检，不一致强制 Whisper"
```

---

## Task 4: 限流/风控重试优化（指数退避 + 412 刷新 cookie）

**Files:**
- Modify: `scripts/api_client.py`、`scripts/bili.py`、`scripts/config.py`
- Test: `tests/test_api_client.py`、`tests/test_config.py`

- [ ] **Step 1: 写失败测试**

`tests/test_api_client.py` 追加：

```python
RETRYABLE = {412, 429, 500, 502, 503, 504}

class RetrySession:
    """可编排响应序列的 Session；记录调用次数与主页预热次数"""
    def __init__(self, responses):
        self.responses, self.calls, self.homepage_calls = list(responses), 0, 0
        self.headers, self.cookies = {}, FakeCookies()
    def get(self, path, params=None, timeout=None):
        if "www.bilibili.com" in path and "api.bilibili" not in path:
            self.homepage_calls += 1
            return FakeResponse({"ok": True})
        self.calls += 1
        status, payload = self.responses.pop(0)
        return FakeResponse(payload, status_code=status)

def _no_sleep(monkeypatch):
    import api_client as ac
    monkeypatch.setattr(ac.time, "sleep", lambda s: None)
    monkeypatch.setattr(ac.random, "uniform", lambda a, b: 0.0)

def test_get_retries_on_412_then_succeeds(monkeypatch):
    _no_sleep(monkeypatch)
    sess = RetrySession([(412, "风控"), (412, "风控"), (200, {"code": 0, "data": {"ok": 1}})])
    client = ApiClient(sess, warm=False, max_retries=3, retry_base_delay=0.01)
    assert client._get("/x/player/v2", {}) == {"ok": 1}
    assert sess.calls == 3 and sess.homepage_calls >= 1  # 412 后刷新 cookie

def test_get_gives_up_after_retries(monkeypatch):
    _no_sleep(monkeypatch)
    sess = RetrySession([(412, "风控")] * 5)
    client = ApiClient(sess, warm=False, max_retries=4, retry_base_delay=0.01)
    with pytest.raises(BiliError) as e:
        client._get("/x/player/v2", {})
    assert e.value.code == 412 and sess.calls == 5  # 1 次 + 4 重试（D4 决策）

def test_get_business_error_not_retried(monkeypatch):
    _no_sleep(monkeypatch)
    sess = RetrySession([(200, {"code": -101, "message": "未登录"})])
    client = ApiClient(sess, warm=False)
    with pytest.raises(BiliError):
        client._get("/x/web-interface/nav", {})
    assert sess.calls == 1  # 业务错误不重试

def test_get_html_body_retried(monkeypatch):
    _no_sleep(monkeypatch)
    sess = RetrySession([(200, "<html>风控</html>"), (200, {"code": 0, "data": {"ok": 2}})])
    client = ApiClient(sess, warm=False, retry_base_delay=0.01)
    assert client._get("/x/player/v2", {}) == {"ok": 2}
    assert sess.calls == 2

def test_config_retry_defaults(tmp_path, monkeypatch):
    import config as cfg
    monkeypatch.setattr(cfg, "CONFIG_PATH", str(tmp_path / "none.json"))
    c = cfg.load_config()
    assert c["api_retries"] == 4 and c["api_retry_base"] == 1.5  # D4：默认 4 次
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_api_client.py tests/test_config.py -q`
Expected: FAIL（重试逻辑/配置缺失）

- [ ] **Step 3: 实现**

`scripts/api_client.py` 顶部：

```python
import random
import time

RETRYABLE_STATUS = frozenset({412, 429, 500, 502, 503, 504})
```

`ApiClient.__init__` 增加参数与字段：

```python
    def __init__(self, session: requests.Session = None, timeout: int = 15, warm: bool = True,
                 max_retries: int = 4, retry_base_delay: float = 1.5):
        ...
        self.max_retries = max(0, int(max_retries))
        self.retry_base_delay = max(0.0, float(retry_base_delay))
```

`_warm_cookie` 支持强制刷新：

```python
    def _warm_cookie(self, force: bool = False) -> None:
        """访问主页预热 buvid3 cookie（无 cookie 裸请求返回 412）；force=True 时强制重取"""
        cookies = getattr(self.session, "cookies", None)
        if not force and cookies is not None and cookies.get("buvid3"):
            return
        params = {"_": int(time.time())} if force else None
        try:
            self.session.get("https://www.bilibili.com", params=params, timeout=self.timeout)
        except Exception:
            pass
```

`_get` 重写（统一重试循环，`envelope=False` 用于字幕裸 JSON）：

```python
    def _backoff_delay(self, attempt: int) -> float:
        """指数退避 + 抖动：base * 2^attempt + U(0, 0.4)"""
        return self.retry_base_delay * (2 ** attempt) + random.uniform(0, 0.4)

    def _get(self, url_or_path: str, params: dict = None, envelope: bool = True):
        """带限流重试的 GET：仅对 412/429/5xx、超时/连接错误、风控 HTML 重试；
        业务错误码（code != 0）不重试。envelope=True 时解包 {code,data} 信封。"""
        url = url_or_path if url_or_path.startswith("http") else BASE + url_or_path
        last = None
        rate_limited = False
        for attempt in range(self.max_retries + 1):
            if attempt:
                time.sleep(self._backoff_delay(attempt - 1))
                if rate_limited:
                    self._warm_cookie(force=True)  # 412 风控：刷新 buvid3 再试
            rate_limited = False
            try:
                resp = self.session.get(url, params=params, timeout=self.timeout)
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
                last = e
                continue
            if resp.status_code in RETRYABLE_STATUS:
                rate_limited = resp.status_code == 412
                last = BiliError(resp.status_code, f"HTTP {resp.status_code}（限流/风控）")
                continue
            resp.raise_for_status()
            try:
                data = resp.json()
            except ValueError:
                last = BiliError(resp.status_code,
                                 f"响应不是 JSON（HTTP {resp.status_code}，可能被风控拦截）")
                rate_limited = True  # 风控页 → 重试并刷新 cookie
                continue
            if envelope:
                if data.get("code") != 0:
                    raise BiliError(data.get("code", -1), data.get("message", "未知错误"))
                return data["data"]
            return data
        if isinstance(last, BiliError):
            raise last
        raise BiliError(-1, f"请求失败（已重试 {self.max_retries} 次）：{str(last)[:120]}")
```

`_fetch_subtitle_once` 中的裸 JSON 下载改用 `_get(..., envelope=False)`：

```python
        payload = self._get(url, envelope=False)  # 字幕文件是裸 JSON（无 code/data 信封）
```

`scripts/config.py` 增加默认值：

```python
    "api_retries": 4,          # 接口限流/风控重试次数（指数退避；D4 决策：默认 4 次）
    "api_retry_base": 1.5,     # 退避基数（秒）
```

`scripts/bili.py` `cmd_run` 中 client 构造（先读 config 再建 client）：

```python
def cmd_run(args, client: ApiClient = None):
    cfg = config.load_config()
    client = client or ApiClient(max_retries=cfg.get("api_retries", 4),
                                 retry_base_delay=cfg.get("api_retry_base", 1.5))
```

- [ ] **Step 4: 全量测试**

Run: `python -m pytest tests/ -q`
Expected: PASS（既有字幕测试中 `time.sleep` 语义不敏感）

- [ ] **Step 5: 提交**

```bash
git add scripts/api_client.py scripts/config.py scripts/bili.py tests/test_api_client.py tests/test_config.py
git commit -m "perf(api): 限流指数退避重试 + 412 风控刷新 cookie（可配置重试参数）"
```

---

## Task 5: Whisper 调优（长视频默认禁 VAD + 默认 medium）

**Files:**
- Modify: `scripts/config.py`、`scripts/transcriber.py`、`scripts/bili.py`
- Test: `tests/test_transcriber.py`、`tests/test_config.py`、`tests/test_cli.py`

- [ ] **Step 1: 写失败测试**

`tests/test_config.py`：`test_default_config` 断言 `whisper_model == "medium"`；新增 `test_whisper_policy_defaults`（`whisper_vad == "auto"`、`vad_long_sec == 1800`）；`test_ensure_creates_default` 断言同步改 medium。

`tests/test_transcriber.py` 追加：

```python
class _VadRecorder:
    """记录每次 transcribe 的 vad_filter 取值"""
    def __init__(self, recorder_list): self.calls = recorder_list
    def __new__(cls, *a, **k):
        obj = super().__new__(cls)
        return obj

def _fake_model_factory(calls, texts=None):
    class FakeModel:
        def __init__(self, *a, **k): pass
        def transcribe(self, audio_path, language="zh", vad_filter=False, log_progress=None, progress_callback=None):
            calls.append(vad_filter)
            if texts is not None:
                return texts, None
            class S: pass
            s = S(); s.start, s.end, s.text = 0.0, 1.0, "ok"
            return [s], None
    return FakeModel

def test_vad_plan_forced_on():
    assert transcriber._vad_plan(True, 7200) == [True]
    assert transcriber._vad_plan(False, 10) == [False]

def test_vad_plan_long_video_default_off():
    assert transcriber._vad_plan(None, 7200) == [False]          # 长视频默认禁 VAD（不回退）
    assert transcriber._vad_plan(None, 600) == [True, False]     # 短视频保持原兜底

def test_transcribe_long_video_disables_vad(monkeypatch):
    calls = []
    monkeypatch.setattr(transcriber, "_load_whisper", lambda: _fake_model_factory(calls))
    monkeypatch.setattr(transcriber, "detect_device", lambda: "cpu")
    transcriber.transcribe("x.m4s", duration_sec=7200)
    assert calls == [False]

def test_transcribe_vad_manual_on(monkeypatch):
    calls = []
    monkeypatch.setattr(transcriber, "_load_whisper", lambda: _fake_model_factory(calls))
    monkeypatch.setattr(transcriber, "detect_device", lambda: "cpu")
    transcriber.transcribe("x.m4s", duration_sec=7200, vad=True)
    assert calls == [True]

def test_default_model_is_medium():
    import inspect
    assert inspect.signature(transcriber.transcribe).parameters["model_size"].default == "medium"
```

`tests/test_cli.py` 追加：

```python
def test_vad_flag_reaches_transcriber(tmp_path, monkeypatch):
    import transcriber as tr_mod
    seen = {}
    def spy(client, bvid, cid, **kw):
        seen.update(kw)
        return [{"start": 0.0, "end": 1.0, "text": "w"}]
    monkeypatch.setattr(tr_mod, "transcribe_video", spy)
    out = tmp_path / "out"
    bili.cmd_run(argparse.Namespace(input="BV1GJ411x7h7", page=1, out=str(out),
                                    no_whisper=False, pick=1, lang=None, model=None, vad=True),
                 client=FakeClient(lines=None))
    assert seen["vad"] is True

def test_whisper_default_model_medium(tmp_path, monkeypatch):
    import transcriber as tr_mod
    seen = {}
    def spy(client, bvid, cid, **kw):
        seen.update(kw)
        return [{"start": 0.0, "end": 1.0, "text": "w"}]
    monkeypatch.setattr(tr_mod, "transcribe_video", spy)
    out = tmp_path / "out"
    bili.cmd_run(argparse.Namespace(input="BV1GJ411x7h7", page=1, out=str(out),
                                    no_whisper=False, pick=1, lang=None, model=None),
                 client=FakeClient(lines=None))
    assert seen["model_size"] == "medium"
```

同时更新既有 monkeypatch 的 `transcribe_video` 假函数签名为 `(*a, **k)`（新 kwargs 透传），以及 `test_model_param_priority_over_config` 改为接收 `**kwargs`。

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_transcriber.py tests/test_config.py tests/test_cli.py -q`

- [ ] **Step 3: 实现**

`scripts/config.py`：

```python
    "whisper_model": "medium",  # 默认 medium；用户可降级 tiny/small 或升级 large-v3
    "whisper_vad": "auto",      # auto=长视频默认关、短视频开；true/false 强制
    "vad_long_sec": 1800,       # “长视频”阈值（秒）：≥ 该值时默认禁用 VAD
```

`scripts/transcriber.py`：

```python
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
```

`transcribe`/`_transcribe_with`/`transcribe_video` 签名与调用：

```python
def transcribe(audio_path: str, model_size: str = "medium", language: str = "zh",
               progress_callback=None, vad=None, duration_sec: float = None,
               vad_long_sec: float = VAD_LONG_SEC_DEFAULT) -> list:
    """...（原 docstring 基础上补充：vad 显式开关；长视频默认禁用 VAD）"""
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
        lines = [...]
        if lines:
            return lines
    return []

def transcribe_video(client, bvid: str, cid: int, model_size: str = "medium",
                     progress_callback=None, vad=None, duration_sec: float = None,
                     vad_long_sec: float = VAD_LONG_SEC_DEFAULT) -> list:
    url = client.get_audio_url(bvid, cid)
    with tempfile.TemporaryDirectory() as td:
        audio_path = download_audio(url, os.path.join(td, "audio.m4s"))
        return transcribe(audio_path, model_size=model_size, progress_callback=progress_callback,
                          vad=vad, duration_sec=duration_sec, vad_long_sec=vad_long_sec)
```

`scripts/bili.py` `_process_page` Whisper 分支：

```python
            cfg = config.load_config()
            mode = cfg.get("whisper_vad", "auto")
            vad_mode = vad if vad is not None else (None if mode == "auto" else bool(mode))
            lines = transcriber.transcribe_video(client, info.bvid, page_obj.cid,
                                                 model_size=model_size or cfg.get("whisper_model") or "medium",
                                                 progress_callback=_progress,
                                                 vad=vad_mode,
                                                 duration_sec=page_obj.duration,
                                                 vad_long_sec=cfg.get("vad_long_sec", 1800))
```

`_process_page` 签名追加 `vad=None`；`_run_batch` 与 `cmd_run` 调用处传 `vad=getattr(args, "vad", None)`。

CLI（`main()` 的 `run` 子命令）：

```python
    gv = run.add_mutually_exclusive_group()
    gv.add_argument("--vad", dest="vad", action="store_true",
                    help="强制开启 Whisper VAD（长视频默认关闭，可手动打开）")
    gv.add_argument("--no-vad", dest="vad", action="store_false", help="强制关闭 Whisper VAD")
    run.set_defaults(vad=None)
```

- [ ] **Step 4: 全量测试**

Run: `python -m pytest tests/ -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add scripts/config.py scripts/transcriber.py scripts/bili.py tests/test_transcriber.py tests/test_config.py tests/test_cli.py
git commit -m "feat(whisper): 长视频默认禁用 VAD（--vad 手动开启）+ 默认模型 medium"
```

---

## Task 6: 文档同步 + 全量回归

**Files:**
- Modify: `SKILL.md`、`README.md`

- [ ] **Step 1: SKILL.md 更新点**

1. §0 配置：`whisper_model` 默认 **medium**（可 `--model` 降级/升级）；新增 `whisper_vad: auto`（长视频默认禁 VAD，`--vad` 手动开）/ `vad_long_sec`；新增 `api_retries`/`api_retry_base`；`vision.subtitle_check`（`auto`/`always`/`off`）——**明确写出「默认 auto（防线初筛）；用户可自选 always/off，以用户选择为准」**。
2. §2 获取内容：字幕校验描述改为「四道防线分级（已放宽阈值：覆盖 ≥50%、关键词零命中判定需 ≥4 词）+ 多模态归属复检」：可疑（软信号）→ 首/中/尾 3 帧复检 → 一致采信 / 不一致强制 Whisper；硬信号（时长越界）直接 Whisper；复检不可用回退原策略。
3. §5 批量：`--vad` / `--no-vad` 说明。
4. `scripts/doctor.py` 增加 vision 行（配置 vision 后输出当前复检模式与可选项，落实「配置后告知用户可自选模式」）：

```python
def check_vision() -> dict:
    """vision 配置状态与字幕复检模式（告知用户可自选）"""
    try:
        import config
        v = (config.load_config().get("vision") or {})
    except Exception:
        v = {}
    return {"enabled": bool(v.get("enabled")),
            "subtitle_check": v.get("subtitle_check", "auto")}

# doctor.main 输出区追加：
    vi = check_vision()
    if vi["enabled"]:
        print(f"👁️ vision：已启用；字幕复检模式={vi['subtitle_check']}"
              "（可选 auto/always/off，以你的选择为准）")
```

配套测试：`tests/test_doctor.py` 用 monkeypatch 让 `config.load_config` 返回 `{"vision": {"enabled": True, "subtitle_check": "auto"}}`，断言 `check_vision()["subtitle_check"] == "auto"`。

- [ ] **Step 2: README.md 更新点**

1. 特征表：字幕防线一行改为「四道防线（分级）+ 多模态归属复检（3 帧，不一致强制 Whisper）」；新增 Whisper 策略行。
2. CLI 表：补 `--vad`/`--no-vad`。
3. 「第三条路径：多模态复检」小节：补字幕归属复检（`subtitle_check` auto/always/off，3 帧成本说明）。
4. 更新日志小节：追加本次条目（字幕分级复检、限流重试、VAD/medium）。

- [ ] **Step 3: 全量回归 + 环境自检**

Run: `python -m pytest tests/ -q`
Expected: `N passed`

Run: `python scripts/bili.py doctor`
Expected: 输出环境检查（cookie/GPU/whisper/模型缓存），无 crash

- [ ] **Step 4: 自查 diff 后提交**

```bash
git diff --stat
git add SKILL.md README.md
git commit -m "docs: 同步字幕复检/限流重试/VAD 与 medium 默认的说明"
```

---

## 验证清单（完成前必须逐项确认）

- [ ] `python -m pytest tests/ -q` 全绿（新增用例含：分级判定、归属复检、重试、VAD 计划、默认模型）
- [ ] 现有 121 个测试无一删除/削弱（仅按新签名更新猴补丁）
- [ ] `vision.subtitle_check` 三态（auto/always/off）行为有测试
- [ ] 硬不可信（越界）不调用多模态（省成本）有测试
- [ ] 412 重试会刷新 cookie、业务错误不重试 有测试
- [ ] `--vad`/`--no-vad` 互斥、透传到 transcriber 有测试
- [ ] 放宽阈值有双向测试：53% 覆盖不误杀 / 45% 覆盖仍可疑；<4 词标题不查零命中 / ≥4 词零命中仍可疑
- [ ] `doctor` 输出 vision 复检模式（配置后告知用户可自选）有测试
- [ ] SKILL.md / README.md 与代码行为同步（默认值、CLI、配置键、模式自选说明）
- [ ] 提交信息符合 Conventional Commits（中文 subject），按任务粒度拆分
- [ ] （可选）真实视频冒烟：`python scripts/bili.py run <带字幕BV> --out <tmp>` 验证字幕路径不被误杀

## 提交计划（顺序）

1. `feat(api): 字幕校验分级（ok/suspect/invalid）并保留可疑候选供多模态复检`
2. `feat(vision): 字幕归属复检（首/中/尾 3 帧定点抽帧 + 一致/不一致判定）`
3. `feat(bili): 可疑字幕接入多模态归属复检，不一致强制 Whisper`
4. `perf(api): 限流指数退避重试 + 412 风控刷新 cookie（可配置重试参数）`
5. `feat(whisper): 长视频默认禁用 VAD（--vad 手动开启）+ 默认模型 medium`
6. `docs: 同步字幕复检/限流重试/VAD 与 medium 默认的说明`
7. （待确认）`chore(glossary): 沉淀 Hermes/DSH 等术语与勘误`（本机新增 59 行数据）

## 后续（本计划之外）

- **Everything-learn 大升级**：按用户需求，在本轮修复落地后单独走 `brainstorming → writing-plans`：调研国内外可复用平台（YouTube/小红书/知乎/播客等）→ 抽象「内容源适配层」→ 复用现有字幕/转写/复检/报告工作流。
