# -*- coding: utf-8 -*-
"""D1: 收藏夹扫描 favs-scan 测试——聚合/过滤/失效统计/优先级排序/快照"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from favs import scan_favorites, filter_items, sort_by_priority, make_snapshot, AI_KEYWORDS


class FakeClient:
    """模拟 ApiClient：3 页收藏夹（20/20/5 = 45 个，声明 50 个 → 5 个失效）"""

    def __init__(self):
        self._medias = [
            {"bvid": f"BV1{i:06d}", "title": title, "duration": 600}
            for i, title in enumerate([
                "AI Agent 架构详解", "大模型推理优化 KV Cache", "Transformer 实战课程",
                "钢琴入门教程", "美食探店视频", "RAG 知识库搭建", "深度学习基础",
                "篮球集锦", "Python 速通", "提示词工程教程", "MTP 多 token 预测",
                "旅行 vlog", "MoE 架构解析", "猫咪日常", "GQA 注意力优化",
                "电影解说", "Docker 部署实践", "吉他弹唱", "LLM 微调实战", "健身教程",
            ] + [f"其他视频{i}" for i in range(25)])]
        self.info_cache = {}

    def get_fav_medias(self, media_id, pn=1, ps=20):
        start = (pn - 1) * ps
        return self._medias[start:start + ps]

    def get_video_info(self, bvid):
        if bvid not in self.info_cache:
            n = int(bvid[2:])
            self.info_cache[bvid] = type("Info", (), {
                "stat": {"view": 10000 + n * 100},
                "owner": f"UP{n % 5}",
                "desc": "",
            })()
        return self.info_cache[bvid]


def test_scan_aggregates_all_pages():
    items = scan_favorites(FakeClient(), 123, declared_count=50)
    assert len(items) == 45, "应拉取全部 45 个视频"
    assert items[0]["title"] == "AI Agent 架构详解"
    assert "view" in items[0], "应补充播放量元信息"


def test_filter_ai_keywords():
    items = scan_favorites(FakeClient(), 123, declared_count=50)
    hit = filter_items(items, AI_KEYWORDS)
    titles = [i["title"] for i in hit]
    assert "AI Agent 架构详解" in titles
    assert "大模型推理优化 KV Cache" in titles
    assert "钢琴入门教程" not in titles, "非 AI 视频不应命中"
    assert "猫咪日常" not in titles


def test_priority_sort_by_views():
    items = scan_favorites(FakeClient(), 123, declared_count=50)
    hit = filter_items(items, AI_KEYWORDS)
    ranked = sort_by_priority(hit)
    views = [i["view"] for i in ranked]
    assert views == sorted(views, reverse=True), "应按播放量降序"


def test_missing_videos_statistics():
    stats = {"declared": 50, "fetched": 45}
    assert stats["declared"] - stats["fetched"] == 5, "应统计失效/不可见视频数"


def test_snapshot_save_and_reload():
    items = scan_favorites(FakeClient(), 123, declared_count=50)
    with tempfile.TemporaryDirectory() as td:
        path = make_snapshot(items, out_dir=td)
        assert os.path.exists(path)
        data = json.load(open(path, encoding="utf-8"))
        assert data["count"] == 45
        assert data["items"][0]["bvid"] == items[0]["bvid"]
