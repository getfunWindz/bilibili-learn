# -*- coding: utf-8 -*-
"""测试隔离：每个测试使用独立缓存目录，防止 (bvid, cid) 缓存跨测试串扰"""
import pytest


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    import subcache
    monkeypatch.setattr(subcache, "CACHE_DIR", str(tmp_path / "cache"))
    yield
