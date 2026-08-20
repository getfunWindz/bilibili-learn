import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import pytest
from api_client import ApiClient, BiliError, VideoInfo

class FakeResponse:
    def __init__(self, payload, status_code=200):
        self.status_code = status_code
        if isinstance(payload, str):
            self.text = payload
        else:
            self.text = json.dumps(payload, ensure_ascii=False)
    def json(self): return json.loads(self.text)
    def raise_for_status(self): pass

class FakeCookies:
    def __init__(self): self._d = {}
    def get(self, k, default=None): return self._d.get(k, default)
    def set(self, k, v, domain=None): self._d[k] = v
    def items(self): return self._d.items()
    def __iter__(self): return iter(self._d)

class FakeSession:
    """handler(path, params) -> dict（API payload）；path 为相对路径或绝对字幕 URL"""
    BASE = "https://api.bilibili.com"
    def __init__(self, handler): self._handler = handler; self.headers = {}; self.cookies = FakeCookies()
    def get(self, path, params=None, timeout=None):
        if path.startswith(self.BASE):
            path = path[len(self.BASE):]
        return FakeResponse(self._handler(path, params or {}))

VIEW_PAYLOAD = {"code": 0, "data": {
    "bvid": "BV1GJ411x7h7", "aid": 170001, "title": "测试视频",
    "owner": {"name": "测试UP"}, "duration": 300, "pubdate": 1600000000,
    "pages": [
        {"cid": 1001, "page": 1, "part": "P1 基础", "duration": 100},
        {"cid": 1002, "page": 2, "part": "P2 进阶", "duration": 200},
    ],
}}

def make_client(view=VIEW_PAYLOAD, subtitles=None, sub_body=None, search=None, playurl=None):
    def handler(path, params):
        if path == "/x/web-interface/nav":
            return {"code": 0, "data": {"wbi_img": {
                "img_url": "https://i0.hdslb.com/bfs/wbi/7cd084941338484aae1ad9425b84077c.png",
                "sub_url": "https://i0.hdslb.com/bfs/wbi/4932caff0ff746eab6f01bf08b70ac45.png"}}}
        if path == "/x/web-interface/view": return view
        if path == "/x/player/v2": return {"code": 0, "data": {"subtitle": {"subtitles": subtitles or []}}}
        if path.startswith("https://"): return {"font_size": 0.4, "body": sub_body or []}  # 字幕文件是裸 JSON，无 code 信封
        if path == "/x/web-interface/search/type": return {"code": 0, "data": {"result": search or []}}
        if path == "/x/web-interface/wbi/search/type": return {"code": 0, "data": {"result": search or []}}
        if path == "/x/player/playurl": return {"code": 0, "data": {"dash": {"audio": playurl or []}}}
        raise AssertionError(f"unexpected path: {path}")
    return ApiClient(FakeSession(handler))

def test_video_info_parsing():
    info = make_client().get_video_info(bvid="BV1GJ411x7h7")
    assert info.title == "测试视频" and info.owner == "测试UP"
    assert info.page_count == 2
    assert info.page_by_index(2).cid == 1002 and info.page_by_index(2).part == "P2 进阶"

def test_video_info_stats_parsing():
    """C3：view 接口的 stat（播放/点赞/收藏）与 desc（简介）进入 VideoInfo"""
    payload = {"code": 0, "data": dict(VIEW_PAYLOAD["data"],
                desc="这是一个测试简介",
                stat={"view": 12345, "like": 678, "favorite": 90, "coin": 12, "share": 3, "danmaku": 456})}
    info = make_client(view=payload).get_video_info(bvid="BV1GJ411x7h7")
    assert info.desc == "这是一个测试简介"
    assert info.stat["view"] == 12345 and info.stat["like"] == 678
    assert info.stat.get("favorite") == 90

def test_page_out_of_range():
    client = make_client()
    with pytest.raises(BiliError):
        client.get_video_info(bvid="BV1GJ411x7h7").page_by_index(5)

def test_subtitle_flow():
    subs = [{"lan": "ai-zh", "lan_doc": "中文（自动生成）", "subtitle_url": "//s1.hdslb.com/x.json"}]
    body = [{"from": i * 1.0, "to": i * 1.0 + 0.9, "content": f"第{i}句"} for i in range(12)]
    client = make_client(subtitles=subs, sub_body=body)
    lines = client.get_subtitle_text("BV1GJ411x7h7", 1001)
    assert len(lines) == 12 and lines[0].content == "第0句" and lines[0].start == 0.0

def test_subtitle_incomplete_coverage_falls_back():
    """字幕时间覆盖不足（如错乱歌词）→ 返回 []，降级 Whisper"""
    subs = [{"lan": "ai-zh", "lan_doc": "中文", "subtitle_url": "//s1.hdslb.com/x.json"}]
    body = [{"from": i * 1.0, "to": i * 1.0 + 0.9, "content": f"歌词{i}"} for i in range(12)]  # 覆盖到 12 秒
    client = make_client(subtitles=subs, sub_body=body)
    lines = client.get_subtitle_text("BV1GJ411x7h7", 1001, duration=900)  # 视频 15 分钟
    assert lines == []  # 12s < 900*0.6 → 判定不可信

def test_subtitle_overcoverage_falls_back():
    """字幕时间远超视频时长（错位字幕）→ 返回 []，降级 Whisper"""
    subs = [{"lan": "ai-zh", "lan_doc": "中文", "subtitle_url": "//s1.hdslb.com/x.json"}]
    body = [{"from": i * 10.0, "to": i * 10.0 + 9.0, "content": f"错位内容{i}"} for i in range(12)]  # 覆盖到 120 秒
    client = make_client(subtitles=subs, sub_body=body)
    lines = client.get_subtitle_text("BV1GJ411x7h7", 1001, duration=60)  # 视频只有 60 秒
    assert lines == []  # 120s > 60*1.3 → 判定不可信

def test_subtitle_requires_two_stable_attempts():
    """连续两次获取内容一致才算可信，且确认确实请求了两次"""
    subs = [{"lan": "ai-zh", "lan_doc": "中文", "subtitle_url": "//s1.hdslb.com/x.json"}]
    body = [{"from": i * 1.0, "to": i * 1.0 + 0.9, "content": f"稳定{i}"} for i in range(12)]
    calls = {"n": 0}
    def handler(path, params):
        if path == "/x/web-interface/nav":
            return {"code": 0, "data": {"wbi_img": {"img_url": "https://i0.hdslb.com/bfs/wbi/7cd084941338484aae1ad9425b84077c.png", "sub_url": "https://i0.hdslb.com/bfs/wbi/4932caff0ff746eab6f01bf08b70ac45.png"}}}
        if path.startswith("https://") and "hdslb" in path:
            calls["n"] += 1
            return {"font_size": 0.4, "body": body}
        if path == "/x/player/v2": return {"code": 0, "data": {"subtitle": {"subtitles": subs}}}
        raise AssertionError(f"unexpected path: {path}")
    client = ApiClient(FakeSession(handler))
    lines = client.get_subtitle_text("BV1GJ411x7h7", 1001)
    assert len(lines) == 12 and lines[0].content == "稳定0"
    assert calls["n"] == 2  # 验证了两次一致性

def test_subtitle_unstable_falls_back_empty(monkeypatch):
    """两次内容不一致 → 返回空，让上层走 Whisper（宁可慢不可错）"""
    subs = [{"lan": "ai-zh", "lan_doc": "中文", "subtitle_url": "//s1.hdslb.com/x.json"}]
    calls = {"n": 0}
    def flaky_body(path, params):
        calls["n"] += 1
        if calls["n"] % 2 == 1:
            return [{"from": 0.0, "to": 1.0, "content": "第一次内容"}]
        return [{"from": 0.0, "to": 1.0, "content": "第二次不同内容"}]
    def handler(path, params):
        if path == "/x/web-interface/nav":
            return {"code": 0, "data": {"wbi_img": {"img_url": "https://i0.hdslb.com/bfs/wbi/7cd084941338484aae1ad9425b84077c.png", "sub_url": "https://i0.hdslb.com/bfs/wbi/4932caff0ff746eab6f01bf08b70ac45.png"}}}
        if path.startswith("https://") and "hdslb" in path:
            return {"font_size": 0.4, "body": flaky_body(path, params)}
        if path == "/x/player/v2": return {"code": 0, "data": {"subtitle": {"subtitles": subs}}}
        raise AssertionError(f"unexpected path: {path}")
    client = ApiClient(FakeSession(handler))
    assert client.get_subtitle_text("BV1GJ411x7h7", 1001) == []

def test_no_subtitle_returns_empty():
    client = make_client(subtitles=[])
    assert client.get_subtitle_text("BV1GJ411x7h7", 1001) == []

def test_search_cleans_html():
    results = [{"bvid": "BV1xx", "title": "Python<em class=\"keyword\">教程</em>", "author": "某UP", "duration": "10:00"}]
    client = make_client(search=results)
    client.session.cookies.set("SESSDATA", "x")  # 搜索需登录态
    out = client.search("Python 教程")
    assert out[0]["title"] == "Python教程" and out[0]["bvid"] == "BV1xx"

def test_audio_url_picks_highest_bandwidth():
    audios = [{"bandwidth": 100, "baseUrl": "http://low"}, {"bandwidth": 900, "baseUrl": "http://high"}]
    url = make_client(playurl=audios).get_audio_url("BV1GJ411x7h7", 1001)
    assert url == "http://high"

def test_pick_subtitle_by_lang():
    from api_client import ApiClient as A
    subs = [{"lan": "ai-zh", "lan_doc": "中文"}, {"lan": "ai-ja", "lan_doc": "日本語"}, {"lan": "ai-en", "lan_doc": "English"}]
    assert A._pick_subtitle(subs, "ja")["lan"] == "ai-ja"
    assert A._pick_subtitle(subs, "en")["lan"] == "ai-en"
    assert A._pick_subtitle(subs, "zh")["lan"] == "ai-zh"

def test_pick_subtitle_lang_fallback():
    """指定语言不存在 → 回退默认逻辑（中文优先）"""
    from api_client import ApiClient as A
    subs = [{"lan": "ai-ja", "lan_doc": "日本語"}, {"lan": "ai-en", "lan_doc": "English"}]
    assert A._pick_subtitle(subs, "zh")["lan"] == "ai-ja"  # 无中文 → 任意第一个

def test_subtitle_lang_parameter_flow():
    """get_subtitle_text 的 lang 参数透传到字幕选择"""
    subs = [{"lan": "ai-zh", "lan_doc": "中文", "subtitle_url": "//s1.hdslb.com/x.json"},
            {"lan": "ai-ja", "lan_doc": "日本語", "subtitle_url": "//s2.hdslb.com/ja.json"}]
    body_zh = [{"from": i * 1.0, "to": i * 1.0 + 0.9, "content": f"中{i}"} for i in range(12)]
    body_ja = [{"from": i * 1.0, "to": i * 1.0 + 0.9, "content": f"日{i}"} for i in range(12)]
    def handler(path, params):
        if path == "/x/web-interface/nav":
            return {"code": 0, "data": {"wbi_img": {"img_url": "https://i0.hdslb.com/bfs/wbi/7cd084941338484aae1ad9425b84077c.png", "sub_url": "https://i0.hdslb.com/bfs/wbi/4932caff0ff746eab6f01bf08b70ac45.png"}}}
        if path == "/x/player/v2": return {"code": 0, "data": {"subtitle": {"subtitles": subs}}}
        if "ja.json" in path: return {"font_size": 0.4, "body": body_ja}
        if "x.json" in path: return {"font_size": 0.4, "body": body_zh}
        raise AssertionError(path)
    client = ApiClient(FakeSession(handler))
    lines = client.get_subtitle_text("BV1GJ411x7h7", 1001, lang="ja")
    assert lines[0].content == "日0"  # 选中了日文字幕


def test_api_error_raises_bili_error():
    client = ApiClient(FakeSession(lambda p, q: {"code": -404, "message": "视频不存在"}))
    with pytest.raises(BiliError):
        client.get_video_info(bvid="BVxxx")

def test_non_json_response_friendly_error():
    client = ApiClient(FakeSession(lambda p, q: "<!DOCTYPE html><html>风控页</html>"))
    with pytest.raises(BiliError) as e:
        client.get_video_info(bvid="BV1GJ411x7h7")
    assert "JSON" in str(e.value)

def test_warm_tolerates_unexpected_path():
    """FakeSession 无 cookies，warm 访问主页的异常应被静默吞掉"""
    client = make_client()
    assert client.get_video_info(bvid="BV1GJ411x7h7").bvid == "BV1GJ411x7h7"

def test_check_login_when_logged_in():
    def handler(path, params):
        if path == "/x/web-interface/nav":
            return {"code": 0, "data": {"wbi_img": {"img_url": "https://i0.hdslb.com/bfs/wbi/7cd084941338484aae1ad9425b84077c.png", "sub_url": "https://i0.hdslb.com/bfs/wbi/4932caff0ff746eab6f01bf08b70ac45.png"}}}
        raise AssertionError(path)
    client = ApiClient(FakeSession(handler))
    assert client.check_login() is True

def test_check_login_detects_expired():
    def handler(path, params):
        if path == "/x/web-interface/nav":
            return {"code": -101, "message": "账号未登录"}
        raise AssertionError(path)
    client = ApiClient(FakeSession(handler))
    assert client.check_login() is False

def test_mixin_key_matches_documented_vector():
    """bilibili 官方文档已知向量：img_key+sub_key → mixin_key"""
    from api_client import get_mixin_key
    mixin = get_mixin_key("7cd084941338484aae1ad9425b84077c",
                          "4932caff0ff746eab6f01bf08b70ac45")
    assert mixin == "ea1db124af3c7062474693fa704f4ff8"

def test_mixin_key_must_be_32_chars():
    from api_client import get_mixin_key
    mixin = get_mixin_key("a" * 32, "b" * 32)
    assert len(mixin) == 32

def test_load_cookie_from_env(monkeypatch):
    from api_client import load_cookie
    monkeypatch.setenv("BILI_COOKIE", "SESSDATA=abc123")
    c = ApiClient(FakeSession(lambda p, q: {"code": 0, "data": {}}))
    assert load_cookie(c) is True
    assert c.session.cookies.get("SESSDATA") == "abc123"

def test_init_auto_loads_cookie(monkeypatch):
    """ApiClient 初始化时自动注入环境变量 cookie（搜索/字幕均受益）"""
    monkeypatch.setenv("BILI_COOKIE", "SESSDATA=auto123")
    c = ApiClient(FakeSession(lambda p, q: {"code": 0, "data": {}}))
    assert c.session.cookies.get("SESSDATA") == "auto123"

def test_search_without_cookie_raises_guidance(monkeypatch):
    import api_client
    monkeypatch.setattr(api_client, "load_cookie", lambda client: False)  # 隔离真实 .bili_cookie 文件
    monkeypatch.delenv("BILI_COOKIE", raising=False)
    client = make_client()
    with pytest.raises(BiliError) as e:
        client.search("Python 入门")
    assert "cookie" in str(e.value).lower() or "SESSDATA" in str(e.value)
