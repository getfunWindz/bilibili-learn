# -*- coding: utf-8 -*-
"""D1: 收藏夹批处理 favs-scan
- 拉取收藏夹全部视频（自动分页）+ 元信息（播放/UP主）
- 关键词过滤（AI 相关性等主题聚类）
- 播放量优先级排序（建议处理顺序）
- 失效/不可见视频统计（声明数 vs 实际返回数）
- 快照保存（JSON，供后续增量对比）
"""
import json
import os
import time
from datetime import datetime

# 内置主题关键词表（AI/编程学习类，可被 --filter 覆盖）
AI_KEYWORDS = [
    "AI", "人工智能", "机器学习", "深度学习", "大模型", "LLM", "GPT", "ChatGPT",
    "Agent", "RAG", "MCP", "Transformer", "KV", "Cache", "MTP", "MoE", "GQA",
    "Python", "编程", "代码", "算法", "PyTorch", "神经网络", "注意力", "提示词",
    "Embedding", "向量", "微调", "部署", "Docker", "推理", "量化", "训练", "模型",
]

OTHER_KEYWORDS = {
    "音乐/乐器": ["钢琴", "吉他", "弹唱", "编曲", "音乐", "鼓", "贝斯"],
    "生活/娱乐": ["vlog", "猫咪", "美食", "探店", "旅行", "电影", "解说", "集锦"],
    "体育": ["篮球", "健身", "足球", "跑步"],
}


def scan_favorites(client, media_id: int, declared_count: int = 0,
                   ps: int = 20) -> list:
    """拉取收藏夹全部视频 → [{bvid,title,duration,view,owner,desc}]（自动分页 + 元信息）"""
    items, pn = [], 1
    while True:
        page = client.get_fav_medias(media_id, pn=pn, ps=ps)
        if not page:
            break
        items.extend(page)
        if len(page) < ps:
            break
        pn += 1
    # 补充元信息（播放量/UP主），单条失败不影响整体
    for it in items:
        try:
            info = client.get_video_info(it["bvid"])
            it["view"] = info.stat.get("view", 0)
            it["owner"] = info.owner
            it["desc"] = (info.desc or "")[:100]
        except Exception:
            it["view"], it["owner"], it["desc"] = 0, "", ""
        time.sleep(0.3)
    return items


def filter_items(items: list, keywords: list) -> list:
    """按关键词过滤（标题/简介大小写不敏感匹配）"""
    out = []
    for it in items:
        text = (it["title"] + " " + it.get("desc", "")).lower()
        if any(k.lower() in text for k in keywords):
            out.append(it)
    return out


def group_by_theme(items: list) -> dict:
    """主题聚类：按 OTHER_KEYWORDS 归入生活/音乐/体育，剩余归 '未分类'"""
    groups = {"AI/编程": []}
    for name in OTHER_KEYWORDS:
        groups[name] = []
    groups["未分类"] = []
    for it in items:
        text = (it["title"] + " " + it.get("desc", "")).lower()
        matched = False
        for name, kws in OTHER_KEYWORDS.items():
            if any(k.lower() in text for k in kws):
                groups[name].append(it)
                matched = True
                break
        if not matched:
            if any(k.lower() in text for k in AI_KEYWORDS):
                groups["AI/编程"].append(it)
            else:
                groups["未分类"].append(it)
    return groups


def sort_by_priority(items: list) -> list:
    """按播放量降序（处理优先级建议）"""
    return sorted(items, key=lambda i: i.get("view", 0), reverse=True)


def make_snapshot(items: list, out_dir: str = "") -> str:
    """保存快照 JSON → 返回路径（供后续增量对比）"""
    out_dir = out_dir or os.getcwd()
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"favs_snapshot_{datetime.now():%Y%m%d}.json")
    data = {
        "saved_at": datetime.now().isoformat(timespec="seconds"),
        "count": len(items),
        "items": items,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    return path


def cmd_favs_scan(args, client=None) -> None:
    """bili favs-scan <fav_id> [--filter kw1,kw2] [--priority] [--out 目录] [--snapshot]"""
    from api_client import ApiClient
    client = client or ApiClient()
    folders = client.get_fav_folders()
    fav = args.fav
    folder = None
    if fav.isdigit():
        folder = next((f for f in folders if str(f["id"]) == fav), None)
    else:
        folder = next((f for f in folders if fav in f["title"]), None)
    if not folder:
        raise SystemExit(f"未找到收藏夹：{fav}（现有：{', '.join(f['title'] for f in folders)}）")

    print(f"收藏夹「{folder['title']}」声明 {folder.get('media_count', '?')} 个视频，正在拉取…")
    items = scan_favorites(client, folder["id"], folder.get("media_count", 0))
    declared = folder.get("media_count", len(items))
    print(f"实际获取 {len(items)} 个（{declared - len(items)} 个失效/不可见）")

    keywords = [k.strip() for k in (args.filter or ",".join(AI_KEYWORDS)).split(",") if k.strip()]
    hit = filter_items(items, keywords)
    print(f"\n命中主题（{len(hit)}/{len(items)} 个）：")
    ranked = sort_by_priority(hit) if args.priority else hit
    for i, it in enumerate(ranked, 1):
        view = f"{it['view']/10000:.1f}万" if it.get("view", 0) >= 10000 else it.get("view", 0)
        print(f"{i:3d}. [{view}播放] {it['title']}（{it.get('owner','?')}）{it['bvid']}")

    if args.snapshot:
        path = make_snapshot(items, out_dir=args.out or "")
        print(f"\n快照已保存：{path}")
