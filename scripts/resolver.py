"""输入解析：链接 / BV号 / av号 → VideoSpec；名称类输入抛 SearchNeeded"""
import re

class VideoSpec:
    def __init__(self, bvid: str = "", aid: int = 0, page: int = 1, raw: str = ""):
        self.bvid = bvid
        self.aid = aid
        self.page = page
        self.raw = raw

    def __repr__(self):
        return f"VideoSpec(bvid={self.bvid!r}, aid={self.aid}, page={self.page})"

class SearchNeeded(Exception):
    """输入是名称/作者，需要走搜索 API"""

_BV_RE = re.compile(r"BV[0-9A-Za-z]{10}")
_AV_RE = re.compile(r"av(\d+)", re.IGNORECASE)
_PAGE_RE = re.compile(r"[?&]p=(\d+)")

def resolve_input(text: str) -> VideoSpec:
    text = text.strip()
    page = 1
    m = _PAGE_RE.search(text)
    if m:
        page = max(1, int(m.group(1)))
    m = _BV_RE.search(text)
    if m:
        return VideoSpec(bvid=m.group(0), page=page, raw=text)
    m = _AV_RE.search(text)
    if m:
        return VideoSpec(aid=int(m.group(1)), page=page, raw=text)
    raise SearchNeeded(text)
