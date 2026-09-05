"""分P范围解析：'1-10' / '1,3,5-8' → 页码列表"""
import re

def parse_pages(text: str) -> list:
    """'1-10'→[1..10]；'1,3,5-8'→[1,3,5,6,7,8]；非法输入抛 ValueError"""
    out = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            raise ValueError(f"无效的范围：{text!r}")
        m = re.fullmatch(r"(\d+)(?:-(\d+))?", part)
        if not m:
            raise ValueError(f"无效的范围段：{part!r}")
        start = int(m.group(1))
        end = int(m.group(2)) if m.group(2) else start
        if start < 1 or end < start:
            raise ValueError(f"无效的范围段：{part!r}")
        out.extend(range(start, end + 1))
    seen, dedup = set(), []
    for p in out:
        if p not in seen:
            seen.add(p)
            dedup.append(p)
    return dedup
