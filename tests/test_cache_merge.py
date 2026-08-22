# -*- coding: utf-8 -*-
"""C3/D3/D2: 字幕缓存、限流退避、目录合并测试"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import subcache
import mergeutil


# ---------- C3: 字幕/转写缓存 ----------

def test_cache_roundtrip():
    with tempfile.TemporaryDirectory() as td:
        subcache.CACHE_DIR = td
        lines = [{"start": 0.0, "end": 2.0, "text": "缓存测试"}]
        subcache.set_cached("BV1TEST", 123, lines)
        got = subcache.get_cached("BV1TEST", 123)
        assert got == lines


def test_cache_miss_returns_none():
    with tempfile.TemporaryDirectory() as td:
        subcache.CACHE_DIR = td
        assert subcache.get_cached("BV1MISS", 999) is None


def test_cache_corrupt_file_tolerated():
    with tempfile.TemporaryDirectory() as td:
        subcache.CACHE_DIR = td
        path = subcache._cache_path("BV1BAD", 1)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, "w", encoding="utf-8").write("{corrupt json")
        assert subcache.get_cached("BV1BAD", 1) is None, "损坏缓存应容错返回 None"


# ---------- D3: 限流退避 ----------

def test_retry_with_backoff_succeeds_on_retry():
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("临时失败")
        return "ok"

    result = subcache.retry_with_backoff(flaky, attempts=4, base_delay=0)
    assert result == "ok"
    assert calls["n"] == 3


def test_retry_with_backoff_gives_up():
    calls = {"n": 0}

    def always_fail():
        calls["n"] += 1
        raise RuntimeError("永远失败")

    try:
        subcache.retry_with_backoff(always_fail, attempts=3, base_delay=0)
        assert False, "应抛出异常"
    except RuntimeError:
        pass
    assert calls["n"] == 3, "应恰好尝试 attempts 次"


def test_retry_with_backoff_exponential_delays():
    """退避间隔应递增（指数）：base=1 → 1, 2, 4"""
    sleeps = []

    def flaky():
        raise RuntimeError("fail")

    import subcache as sc
    orig = sc.time.sleep
    sc.time.sleep = lambda s: sleeps.append(s)
    try:
        try:
            sc.retry_with_backoff(flaky, attempts=4, base_delay=1)
        except RuntimeError:
            pass
    finally:
        sc.time.sleep = orig
    assert sleeps == [1, 2, 4], f"指数退避失败：{sleeps}"


# ---------- D2: 目录合并 ----------

def _make_p_dir(root, pname, content):
    d = os.path.join(root, pname)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "subtitle.txt"), "w", encoding="utf-8") as f:
        f.write(content)
    return d


def test_merge_copies_missing_pages():
    with tempfile.TemporaryDirectory() as td:
        src = os.path.join(td, "src")
        dst = os.path.join(td, "dst")
        _make_p_dir(src, "P01", "P01内容")
        _make_p_dir(src, "P02", "P02内容")
        _make_p_dir(dst, "P01", "P01旧内容")
        copied, skipped = mergeutil.merge_dirs(src, dst)
        assert copied == ["P02"], f"应复制缺失的 P02：{copied}"
        assert skipped == ["P01"], f"P01 已存在应跳过：{skipped}"
        assert open(os.path.join(dst, "P02", "subtitle.txt"), encoding="utf-8").read() == "P02内容"
        # P01 不被覆盖
        assert open(os.path.join(dst, "P01", "subtitle.txt"), encoding="utf-8").read() == "P01旧内容"
