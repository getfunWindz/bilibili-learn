"""bilibili 官方只读 API 封装（免登录）"""
import re
import requests

BASE = "https://api.bilibili.com"
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"),
    "Referer": "https://www.bilibili.com",
}

# 限流/风控类可重试 HTTP 状态码（其余 4xx 不重试）
RETRYABLE_STATUS = frozenset({412, 429, 500, 502, 503, 504})

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


class SubtitleResult:
    """字幕抓取分级结果：ok=可信 / suspect=软可疑（可多模态复检拯救）/ invalid=硬不可信 / empty=无字幕"""
    def __init__(self, lines: list, status: str, reason: str = ""):
        self.lines, self.status, self.reason = lines, status, reason


# 字幕校验阈值（v3.4 放宽，D3 决策：覆盖下限 60%→50%；关键词零命中判定需 ≥4 词）
COVERAGE_MIN_RATIO = 0.5
COVERAGE_MAX_RATIO = 1.3
MIN_SUBTITLE_LINES = 10
KEYWORD_MIN_COUNT = 4

# wbi 签名打乱表（bilibili 公开算法）
_WBI_TAB = [46,47,18,2,53,8,23,32,15,50,10,31,58,3,45,35,27,43,5,49,33,9,42,19,29,28,14,39,12,38,41,13,37,48,7,16,24,55,40,61,26,17,0,1,60,51,30,4,22,25,54,21,56,59,6,63,57,62,11,36,20,34,44,52]

import hashlib
import os
import random
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
    def __init__(self, session: requests.Session = None, timeout: int = 15, warm: bool = True,
                 max_retries: int = 4, retry_base_delay: float = 1.5):
        self.session = session or requests.Session()
        self.session.headers.update(HEADERS)
        self.timeout = timeout
        self.max_retries = max(0, int(max_retries))
        self.retry_base_delay = max(0.0, float(retry_base_delay))
        if warm:
            try:
                self._warm_cookie()
            except Exception:
                pass  # 预热失败不致命（如测试 FakeSession），后续请求仍会尝试
        try:
            load_cookie(self)  # 自动注入登录 cookie（搜索/AI 字幕均需要）
        except Exception:
            pass

    def _warm_cookie(self, force: bool = False) -> None:
        """访问主页预热 buvid3 cookie（bilibili 对无 cookie 的裸 API 请求返回 412）；force=True 强制重取"""
        cookies = getattr(self.session, "cookies", None)
        if not force and cookies is not None and cookies.get("buvid3"):
            return
        params = {"_": int(time.time())} if force else None
        try:
            self.session.get("https://www.bilibili.com", params=params, timeout=self.timeout)
        except Exception:
            pass  # 预热失败不致命，后续请求仍会尝试

    def _backoff_delay(self, attempt: int) -> float:
        """指数退避 + 抖动：base * 2^attempt + U(0, 0.4)"""
        return self.retry_base_delay * (2 ** attempt) + random.uniform(0, 0.4)

    def _get(self, url_or_path: str, params: dict = None, envelope: bool = True):
        """带限流重试的 GET：仅对 412/429/5xx、超时/连接错误、风控 HTML 重试；
        业务错误码（code != 0）不重试。envelope=True 时解包 {code,data} 信封。
        412 风控重试前强制刷新 buvid3 cookie。"""
        url = url_or_path if url_or_path.startswith("http") else BASE + url_or_path
        last = None
        rate_limited = False
        for attempt in range(self.max_retries + 1):
            if attempt:
                time.sleep(self._backoff_delay(attempt - 1))
                if rate_limited:
                    self._warm_cookie(force=True)
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

    def get_video_info(self, bvid: str = "", aid: int = 0) -> VideoInfo:
        """视频元信息 + 分P列表（批量扩展点：pages 即全部分P）"""
        params = {"bvid": bvid} if bvid else {"aid": aid}
        return VideoInfo(self._get("/x/web-interface/view", params))

    def get_subtitles(self, bvid: str, cid: int) -> list:
        """字幕列表（含 AI 字幕）；空列表 = 无字幕"""
        data = self._get("/x/player/v2", {"bvid": bvid, "cid": cid})
        return (data.get("subtitle") or {}).get("subtitles") or []

    def get_subtitle_text(self, bvid: str, cid: int, duration: int = None, attempts: int = 2,
                          lang: str = None, title: str = "", desc: str = "") -> list:
        """兼容包装：仅返回可信字幕（ok），其余返回 [] 由上层降级/复检"""
        res = self.fetch_subtitle(bvid, cid, duration=duration, lang=lang,
                                  title=title, desc=desc)
        return res.lines if res.status == "ok" else []

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
                if last is not None and last.lines:
                    # 接口闪断：首抓有内容、二抓为空 → 保留候选交多模态复检，而非直接判无字幕
                    return SubtitleResult(last.lines, "suspect", "二次获取为空")
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

    @staticmethod
    def _subtitle_complete(lines: list, duration: int = None) -> bool:
        """字幕完整性：行数足够 + 时间覆盖视频主体（防御错乱歌词/残缺/错位字幕）
        下限：末条 ≥ 50% 时长（v3.4 放宽，原 60%）；上限：末条 ≤ 130% 时长（防错位字幕超长）"""
        if not lines or len(lines) < MIN_SUBTITLE_LINES:
            return False
        if duration:
            last = lines[-1].end
            if last < duration * COVERAGE_MIN_RATIO or last > duration * COVERAGE_MAX_RATIO:
                return False
        return True

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

    @staticmethod
    def _subtitle_plausible(lines: list, title: str = "", desc: str = "",
                            duration: int = None) -> tuple:
        """B1 内容级校验：检测「结构完整但内容错乱」的字幕（本次事故：靶场/影视剧/歌词）。
        返回 (plausible, reason)；plausible=False 表示应降级 Whisper。
        信号：① 歌词特征（♪） ② 标题关键词在字幕中完全未出现 ③ 行密度异常"""
        if not lines:
            return False, "空字幕"
        text = " ".join(l.content for l in lines)
        # ① 歌词特征
        if "♪" in text:
            return False, "歌词特征(♪)"
        # ② 标题关键词重叠：标题提取出的关键词（中文 2-gram + 英文词）
        #    在字幕中完全零命中 → 内容与标题无关（Whisper 音译变体如 kb cash 也能被中文 2-gram 兜住）
        #    v3.4 放宽：仅当关键词 ≥4 个时才判定，避免短标题/泛化标题误杀
        kws = ApiClient._extract_keywords(title)
        if len(kws) >= KEYWORD_MIN_COUNT:
            hits = sum(1 for k in kws if k.lower() in text.lower())
            if hits == 0:
                return False, f"标题关键词零命中({len(kws)}词)"
        # ③ 行密度异常（正常讲解约 0.3~1.0 行/秒；错乱整段字幕常 > 2 行/秒）
        if duration and duration > 0:
            density = len(lines) / duration
            if density > 2.0:
                return False, f"行密度异常({density:.1f}行/秒)"
        return True, ""

    @staticmethod
    def _extract_keywords(text: str) -> list:
        """提取标题关键词：中文滑窗 2-gram（防贪婪整串）+ ≥3 字母英文词（排除纯数字/符号）"""
        kws = []
        for zh in re.findall(r"[\u4e00-\u9fff]{2,}", text):
            for i in range(len(zh) - 1):
                kws.append(zh[i:i + 2])
        kws += [w for w in re.findall(r"[a-zA-Z]{3,}", text) if w.lower() not in {"the", "and"}]
        return kws

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
        url = chosen.get("subtitle_url") or ""
        if not url:
            return []  # 接口偶发返回空 url → 视为无字幕，由上层降级 Whisper
        if url.startswith("//"):
            url = "https:" + url
        elif url.startswith("/"):
            url = BASE + url
        payload = self._get(url, envelope=False)  # 字幕文件是裸 JSON（无 code/data 信封）
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

    def get_video_url(self, bvid: str, cid: int, lowest: bool = False) -> str:
        """视频画面流直链；lowest=True 取最低码率（归属复检抽帧省带宽），默认最高"""
        data = self._get("/x/player/playurl", {"bvid": bvid, "cid": cid, "fnval": 16})
        videos = (data.get("dash") or {}).get("video") or []
        if not videos:
            raise BiliError(-404, "无法获取视频流（可能需要登录）")
        videos.sort(key=lambda v: v.get("bandwidth", 0), reverse=not lowest)
        return videos[0]["baseUrl"]

    def check_login(self) -> bool:
        """检测登录态：nav 接口 code=-101 表示 cookie 过期/未登录"""
        try:
            self._get("/x/web-interface/nav", {})
            return True
        except BiliError as e:
            if e.code == -101:
                return False
            raise

    def get_fav_folders(self) -> list:
        """当前账号的收藏夹列表 → [{id, title, media_count}]；需登录"""
        nav = self._get("/x/web-interface/nav", {})
        mid = nav.get("mid") or 0
        data = self._get("/x/v3/fav/folder/created/list-all", {"up_mid": mid})
        out = []
        for f in (data.get("list") or []):
            out.append({"id": f["id"], "title": f.get("title", ""),
                        "media_count": f.get("media_count", 0)})
        return out

    def get_fav_medias(self, media_id: int, pn: int = 1, ps: int = 20) -> list:
        """收藏夹内容 → [{bvid, title, duration}]；ps 上限 20"""
        ps = min(max(1, ps), 20)  # 接口上限 20，超出会被 -400 拒绝
        data = self._get("/x/v3/fav/resource/list",
                         {"media_id": media_id, "pn": pn, "ps": ps})
        out = []
        for m in (data.get("medias") or []):
            out.append({"bvid": m.get("bvid", ""), "title": m.get("title", ""),
                        "duration": m.get("duration", 0)})
        return out
