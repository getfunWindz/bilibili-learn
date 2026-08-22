# -*- coding: utf-8 -*-
"""C3/D3: 字幕/转写缓存 与 指数退避重试
- 缓存：按 (bvid, cid) 保存字幕/转写结果到 scripts/cache/，重复处理不重复下载/转写
- 重试：批量获取失败时指数退避重试（2s/4s/8s），缓解接口限流
"""
import json
import os
import time

CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache")


def _cache_path(bvid: str, cid: int) -> str:
    return os.path.join(CACHE_DIR, f"{bvid}_{cid}.json")


def get_cached(bvid: str, cid: int) -> list:
    """命中缓存 → [{start,end,text}]；未命中/损坏 → None"""
    path = _cache_path(bvid, cid)
    if not os.path.exists(path):
        return None
    try:
        return json.load(open(path, encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def set_cached(bvid: str, cid: int, lines: list) -> None:
    """写入缓存（原子写：先写临时文件再改名，防中断损坏）"""
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = _cache_path(bvid, cid)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(lines, f, ensure_ascii=False)
    os.replace(tmp, path)


def clear_cache(bvid: str = "", cid: int = 0) -> int:
    """清空全部或指定 (bvid, cid) 缓存；返回删除文件数"""
    if bvid:
        p = _cache_path(bvid, cid)
        if os.path.exists(p):
            os.remove(p)
            return 1
        return 0
    n = 0
    if os.path.isdir(CACHE_DIR):
        for f in os.listdir(CACHE_DIR):
            if f.endswith(".json"):
                os.remove(os.path.join(CACHE_DIR, f))
                n += 1
    return n


def retry_with_backoff(fn, attempts: int = 3, base_delay: float = 2.0):
    """指数退避重试：失败后等待 base_delay * 2^i 秒再试；attempts 次后抛出最后异常"""
    last = None
    for i in range(attempts):
        try:
            return fn()
        except Exception as e:
            last = e
            if i < attempts - 1:
                time.sleep(base_delay * (2 ** i))
    raise last
