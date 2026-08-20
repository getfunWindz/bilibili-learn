"""bilibili 官方只读 API 封装（免登录）"""
import re
import requests

BASE = "https://api.bilibili.com"
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"),
    "Referer": "https://www.bilibili.com",
}

class BiliError(Exception):
    def __init__(self, code, message):
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message

class Page:
    def __init__(self, cid: int, page: int, part: str, duration: int):
        self.cid, self.page, self.part, self.duration = cid, page, part, duration

class VideoInfo:
    def __init__(self, data: dict):
        self.bvid = data["bvid"]
        self.aid = data["aid"]
        self.title = data["title"]
        self.owner = data["owner"]["name"]
        self.duration = data["duration"]
        self.pubdate = data["pubdate"]
        self.desc = data.get("desc", "")
        self.stat = data.get("stat") or {}  # C3：view/like/favorite/coin/share/danmaku
        self.pages = [Page(p["cid"], p["page"], p["part"], p["duration"]) for p in data["pages"]]

    @property
    def page_count(self) -> int:
        return len(self.pages)

    def page_by_index(self, page: int) -> Page:
        if page < 1 or page > len(self.pages):
            raise BiliError(-400, f"页码 {page} 超出范围（共 {len(self.pages)} P）")
        return self.pages[page - 1]

class SubtitleLine:
    def __init__(self, start: float, end: float, content: str):
        self.start, self.end, self.content = start, end, content

    def __eq__(self, other):
        return (isinstance(other, SubtitleLine)
                and self.start == other.start
                and self.end == other.end
                and self.content == other.content)

# wbi 签名打乱表（bilibili 公开算法）
_WBI_TAB = [46,47,18,2,53,8,23,32,15,50,10,31,58,3,45,35,27,43,5,49,33,9,42,19,29,28,14,39,12,38,41,13,37,48,7,16,24,55,40,61,26,17,0,1,60,51,30,4,22,25,54,21,56,59,6,63,57,62,11,36,20,34,44,52]

import hashlib
import os
import time
import urllib.parse


def get_mixin_key(img_key: str, sub_key: str) -> str:
    """wbi 签名密钥：img_key + sub_key 按打乱表重排取前 32 位"""
    raw = img_key + sub_key
    return "".join(raw[i] for i in _WBI_TAB)[:32]


def _get_wbi_keys(session, timeout: int) -> tuple:
    nav = session.get(BASE + "/x/web-interface/nav", timeout=timeout)
    nav.raise_for_status()
    wbi = nav.json()["data"]["wbi_img"]
    img_key = wbi["img_url"].rsplit("/", 1)[1].split(".")[0]
    sub_key = wbi["sub_url"].rsplit("/", 1)[1].split(".")[0]
    return img_key, sub_key


def load_cookie(client) -> bool:
    """从环境变量 BILI_COOKIE 或 scripts/.bili_cookie 注入登录 cookie；有则 True"""
    raw = os.environ.get("BILI_COOKIE", "") or ""
    if not raw:
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".bili_cookie")
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                raw = f.read().strip()
    if not raw:
        return False
    for part in raw.split(";"):
        part = part.strip()
        if "=" in part:
            k, v = part.split("=", 1)
            client.session.cookies.set(k.strip(), v.strip(), domain=".bilibili.com")
    return True

class ApiClient:
    def __init__(self, session: requests.Session = None, timeout: int = 15, warm: bool = True):
        self.session = session or requests.Session()
        self.session.headers.update(HEADERS)
        self.timeout = timeout
        if warm:
            try:
                self._warm_cookie()
            except Exception:
                pass  # 预热失败不致命（如测试 FakeSession），后续请求仍会尝试
        try:
            load_cookie(self)  # 自动注入登录 cookie（搜索/AI 字幕均需要）
        except Exception:
            pass

    def _warm_cookie(self) -> None:
        """访问主页预热 buvid3 cookie（bilibili 对无 cookie 的裸 API 请求返回 412）"""
        cookies = getattr(self.session, "cookies", None)
        if cookies is not None and cookies.get("buvid3"):
            return
        self.session.get("https://www.bilibili.com", timeout=self.timeout)

    def _get(self, path: str, params: dict) -> dict:
        resp = self.session.get(path if path.startswith("http") else BASE + path,
                                params=params, timeout=self.timeout)
        resp.raise_for_status()
        try:
            data = resp.json()
        except ValueError as e:
            raise BiliError(resp.status_code,
                            f"响应不是 JSON（HTTP {resp.status_code}，可能被风控拦截）") from e
        if data.get("code") != 0:
            raise BiliError(data.get("code", -1), data.get("message", "未知错误"))
        return data["data"]

    def get_video_info(self, bvid: str = "", aid: int = 0) -> VideoInfo:
        """视频元信息 + 分P列表（批量扩展点：pages 即全部分P）"""
        params = {"bvid": bvid} if bvid else {"aid": aid}
        return VideoInfo(self._get("/x/web-interface/view", params))

    def get_subtitles(self, bvid: str, cid: int) -> list:
        """字幕列表（含 AI 字幕）；空列表 = 无字幕"""
        data = self._get("/x/player/v2", {"bvid": bvid, "cid": cid})
        return (data.get("subtitle") or {}).get("subtitles") or []

    def get_subtitle_text(self, bvid: str, cid: int, duration: int = None, attempts: int = 2,
                          lang: str = None) -> list:
        """下载字幕全文。接口不稳定（降级/内容错乱）时：
        连续两次内容一致 + 时长覆盖率达标才可信；否则返回 []，由上层降级 Whisper。
        lang: 指定字幕语言（zh/ja/en 等），None 时自动（中文优先）"""
        first = None
        for i in range(max(2, attempts)):
            lines = self._fetch_subtitle_once(bvid, cid, lang)
            if not self._subtitle_complete(lines, duration):
                time.sleep(2)
                continue
            if first is None:
                first = lines
                time.sleep(2)
                continue
            if lines == first:
                return lines  # 两次一致且覆盖完整 → 可信
            return []  # 两次内容不同 → 接口错乱，宁可走 Whisper
        return first or []

    @staticmethod
    def _subtitle_complete(lines: list, duration: int = None) -> bool:
        """字幕完整性：行数足够 + 时间覆盖视频主体（防御错乱歌词/残缺/错位字幕）
        下限：末条 ≥ 60% 时长（防覆盖不足）；上限：末条 ≤ 130% 时长（防错位字幕超长）"""
        if not lines or len(lines) < 10:
            return False
        if duration:
            last = lines[-1].end
            if last < duration * 0.6 or last > duration * 1.3:
                return False
        return True

    @staticmethod
    def _pick_subtitle(subs: list, lang: str = None) -> dict:
        """选择字幕：指定 lang 优先（如 ai-ja / ja-JP）；未指定时中文优先，其次任意"""
        if lang:
            for s in subs:
                if lang.lower() in s.get("lan", "").lower():
                    return s
        for s in subs:
            if "zh" in s.get("lan", "") and not s.get("lan", "").startswith("ai-"):
                return s
        for s in subs:
            if "zh" in s.get("lan", ""):
                return s
        return subs[0]

    def _fetch_subtitle_once(self, bvid: str, cid: int, lang: str = None) -> list:
        """单次获取字幕全文；无字幕返回 []。按 cid 取 → 未来批量多P直接复用"""
        subs = self.get_subtitles(bvid, cid)
        if not subs:
            return []
        chosen = self._pick_subtitle(subs, lang)
        url = chosen["subtitle_url"]
        if url.startswith("//"):
            url = "https:" + url
        elif url.startswith("/"):
            url = BASE + url
        resp = self.session.get(url, timeout=self.timeout)
        resp.raise_for_status()
        payload = resp.json()  # 字幕文件是裸 JSON（无 code/data 信封），不能走 _get
        return [SubtitleLine(float(l["from"]), float(l["to"]), l["content"])
                for l in (payload.get("body") or [])]

    def search(self, keyword: str, limit: int = 5) -> list:
        """搜索视频 → [{bvid,title,author,duration}]；需要登录 cookie（未登录搜索被风控）"""
        if not (self.session.cookies.get("SESSDATA") or load_cookie(self)):
            raise BiliError(-101, "搜索需要登录 cookie：请将 SESSDATA=xxx 写入 scripts/.bili_cookie "
                                  "或设置环境变量 BILI_COOKIE，或直接提供视频链接")
        img_key, sub_key = _get_wbi_keys(self.session, self.timeout)
        params = {"search_type": "video", "keyword": keyword,
                  "page": 1, "page_size": max(1, limit)}
        params["wts"] = int(time.time())
        params["w_rid"] = hashlib.md5(
            (urllib.parse.urlencode(sorted(params.items())) + get_mixin_key(img_key, sub_key)).encode()
        ).hexdigest()
        data = self._get("/x/web-interface/wbi/search/type", params)
        out = []
        for r in (data.get("result") or [])[:limit]:
            out.append({"bvid": r.get("bvid", ""),
                        "title": re.sub(r"<[^>]+>", "", r.get("title", "")),
                        "author": r.get("author", ""),
                        "duration": r.get("duration", "")})
        return out

    def get_audio_url(self, bvid: str, cid: int) -> str:
        """音频直链（Whisper 兜底）；取码率最高一路"""
        data = self._get("/x/player/playurl", {"bvid": bvid, "cid": cid, "fnval": 16})
        audios = (data.get("dash") or {}).get("audio") or []
        if not audios:
            raise BiliError(-404, "无法获取音频流（可能需要登录）")
        audios.sort(key=lambda a: a.get("bandwidth", 0), reverse=True)
        return audios[0]["baseUrl"]

    def check_login(self) -> bool:
        """检测登录态：nav 接口 code=-101 表示 cookie 过期/未登录"""
        try:
            self._get("/x/web-interface/nav", {})
            return True
        except BiliError as e:
            if e.code == -101:
                return False
            raise
