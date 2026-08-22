"""bilibili 学习视频内容获取 CLI"""
import argparse
import json
import logging
import os
import re
import sys
import time
from datetime import datetime

from api_client import ApiClient, BiliError
from resolver import resolve_input, SearchNeeded
import config

VERSION = "0.1.0"
_LOGGER = logging.getLogger("bili")

def setup_logging(logs_dir: str = None) -> str:
    """初始化日志：logs/bili.log（append）。返回日志文件路径"""
    logs_dir = logs_dir or os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
    os.makedirs(logs_dir, exist_ok=True)
    path = os.path.join(logs_dir, "bili.log")
    _LOGGER.handlers.clear()
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    _LOGGER.addHandler(handler)
    _LOGGER.setLevel(logging.INFO)
    return path

def _fmt_ts(ts: int) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(ts))

def _safe_name(title: str, maxlen: int = 60) -> str:
    return re.sub(r'[\\/:*?"<>|\s]+', "_", title).strip("_")[:maxlen] or "untitled"

def cmd_resolve(args):
    try:
        spec = resolve_input(args.input)
        print(json.dumps({"kind": "video", "bvid": spec.bvid, "aid": spec.aid,
                          "page": spec.page}, ensure_ascii=False))
    except SearchNeeded:
        print(json.dumps({"kind": "search", "keyword": args.input}, ensure_ascii=False))

def cmd_search(args):
    client = ApiClient()
    try:
        results = client.search(args.keyword, limit=args.limit)
    except BiliError as e:
        print(f"搜索失败：{e}", file=sys.stderr)
        print("提示：未登录的 bilibili 搜索会被风控。可选：", file=sys.stderr)
        print("  1. 直接提供视频链接或 BV 号（推荐）", file=sys.stderr)
        print("  2. 配置登录 cookie：把 SESSDATA=xxx 写入 scripts/.bili_cookie 或设置环境变量 BILI_COOKIE", file=sys.stderr)
        sys.exit(2)
    if not results:
        print("无搜索结果", file=sys.stderr)
        sys.exit(1)
    for i, r in enumerate(results, 1):
        print(f"{i}. [{r['bvid']}] {r['title']} — UP主: {r['author']}（时长 {r['duration']}）")

def render_template(info: dict) -> str:
    return f"""# {info['title']}

> 视频信息

| 项目 | 内容 |
|------|------|
| 标题 | {info['title']} |
| UP主 | {info['owner']} |
| BV号 | {info['bvid']} |
| 链接 | {info['url']} |
| 总时长 | {info['duration_seconds']} 秒 |
| 发布日期 | {info['pubdate']} |
| 分P | {info['page']}/{info['page_count']} |
| 播放量 | {info['stat']['view']} |
| 点赞 | {info['stat']['like']} |
| 收藏 | {info['stat']['favorite']} |
| 字幕来源 | {info['subtitle_source']} |

> 简介：{info['desc'] or '（无）'}

## 一句话总结

<!-- 用 1-3 句话概括视频核心内容 -->

## 知识点全览

<!-- 学习地图：按出现顺序列出本视频全部知识点（编号），如：
1. PyCharm 安装与 Python 环境配置
2. 第一行代码与两种运行方式
3. 四种基本数据类型（字符串/整型/浮点/布尔）
...（覆盖视频全部内容，不遗漏） -->

## 知识详解

<!-- 按时间轴切段（每段 3~10 分钟，段标题标注时间戳起止），逐段展开：
### 段1｜[00:00-05:12] 段主题

#### 知识点1：知识点名称（[02:30] 首次出现时间）
- **定义**：官方/标准表述（正式定义或教科书式描述）
- **通俗**：一句话人话解释（新手视角）
- **细节**：2~4 条浓缩要点（只留知识本身，删除口播废话/寒暄/重复解释）
- **例子**：视频中的代码/案例（如有）
- **彩蛋**：原作者的趣味原话/小资讯/小tips（可选，有则填；增加学习趣味性）
- **延伸**：相关概念的深入拓展（主流/高级知识点 2~4 条，每条 = 概念 + 使用场景/一句话解释，
  如：列表→切片/列表推导式/排序 sort；字典→视图对象/合并 update/JSON 序列化；
  函数→默认参数/lambda/装饰器；类→继承/多态/魔术方法；简单知识点 1 条或省略）
- **名词注释（R11，必填）**：本知识点中出现的、前置未提及的专业名词逐条注释，
  先查 references/glossary.json 术语库（命中直接引用），未命中新写并自动沉淀入库；格式：
  ```
  **名词注释**：
  - teacher forcing：训练时用真实标签而非模型自身输出作为下一步输入
  - KV Cache：键值缓存，推理时缓存注意力 K/V 矩阵避免重复计算
  ```
  交付前运行 `python scripts/glossary.py check <报告.md> <subtitle.txt>` 校验到无遗漏

#### 知识点2：...
（该时间段内原作者讲到的每个知识点都必须列出，不省略）

### 段2｜[05:12-10:00] 段主题
... -->

## 关键概念 / 术语解释表

| 概念 | 通俗解释 |
|------|---------|
|  |  |

## 金句与重要观点

## 原话摘录

<!-- 从字幕中挑选 5~10 条有教学价值/启发性的原话，逐条附上时间点，如：
- [12:34] “原话内容”
-->

## 学习路径与自测

<!-- 学习收获（按知识点维度）+ 自测问题清单（每 3~5 个知识点出一道自测题，检验是否真的学会） -->

## 元信息

- 生成时间：{info['fetched_at']}
- 工具：bilibili-learn v{VERSION}
"""

def _write_outputs(out_dir: str, info, page, lines, subtitle_src: str) -> None:
    os.makedirs(out_dir, exist_ok=True)
    info_json = {
        "title": info.title, "owner": info.owner, "bvid": info.bvid, "aid": info.aid,
        "url": f"https://www.bilibili.com/video/{info.bvid}?p={page}",
        "duration_seconds": info.duration, "pubdate": _fmt_ts(info.pubdate),
        "desc": info.desc,
        "stat": {k: info.stat.get(k, 0) for k in ("view", "like", "favorite", "coin", "share", "danmaku")},
        "page": page, "page_part": info.page_by_index(page).part,
        "page_count": info.page_count, "subtitle_source": subtitle_src,
        "fetched_at": datetime.now().isoformat(timespec="seconds"), "tool": f"bilibili-learn v{VERSION}",
    }
    with open(os.path.join(out_dir, "video_info.json"), "w", encoding="utf-8") as f:
        json.dump(info_json, f, ensure_ascii=False, indent=2)
    with open(os.path.join(out_dir, "subtitle.txt"), "w", encoding="utf-8") as f:
        for ln in lines:
            f.write(f"[{ln['start']:.1f}-{ln['end']:.1f}] {ln['text']}\n")
    with open(os.path.join(out_dir, "report_template.md"), "w", encoding="utf-8") as f:
        f.write(render_template(info_json))

def _load_resume_pages(out_root: str, bvid: str) -> set:
    """扫描 out_root 下同 bvid 的合集目录（含 UP主 层级），返回 status=ok 的页码集合"""
    root = os.path.abspath(out_root)
    done = set()
    if not os.path.isdir(root):
        return done
    for d in os.listdir(root):
        p = os.path.join(root, d)
        if not os.path.isdir(p):
            continue
        # 兼容两级（UP主/合集）与平铺（合集）结构
        candidates = [os.path.join(p, x) for x in os.listdir(p)] if os.path.isdir(p) else []
        dirs = [d] if d.endswith("_合集") else [x for x in candidates if os.path.isdir(x) and x.endswith("_合集")]
        for vp in dirs:
            if not vp.endswith("_合集"):
                continue
            f = os.path.join(vp, "video_info.json")
            if not os.path.exists(f):
                continue
            try:
                info = json.load(open(f, encoding="utf-8"))
            except Exception:
                continue
            if info.get("bvid") != bvid:
                continue
            for r in info.get("pages") or []:
                if r.get("status") == "ok":
                    done.add(r["page"])
    return done


def _select_pages(args, info) -> list:
    """返回要处理的页码列表（保序去重）"""
    if getattr(args, "all_", False):
        return [p.page for p in info.pages]
    if getattr(args, "pages", None):
        from pagespec import parse_pages
        wanted = parse_pages(args.pages)
        valid = {p.page for p in info.pages}
        skipped = [w for w in wanted if w not in valid]
        if skipped:
            print(f"跳过超出范围的页码：{skipped}（共 {info.page_count} P）", file=sys.stderr)
        pages = [w for w in wanted if w in valid]
        if not pages:
            raise BiliError(-400, "所选页码均超出范围")
        return pages
    return [args.page if args.page is not None else 1]


def _process_page(client, info, page, out_root, no_whisper, single=False, lang=None,
                  model_size=None, no_cache=False):
    """处理单个 P：取字幕/whisper → 落盘。返回 (status, subtitle_source, line_count, out_dir, error)"""
    page_obj = info.page_by_index(page)
    # C3：命中缓存直接复用（避免重复下载/转写）
    if not no_cache:
        import subcache
        cached = subcache.get_cached(info.bvid, page_obj.cid)
        if cached:
            lines = [{"start": l["start"], "end": l["end"], "text": l["text"]} for l in cached]
            if single:
                out_dir = os.path.join(out_root, _safe_name(info.owner),
                                       f"{datetime.now():%Y-%m-%d}_{_safe_name(info.title)}"
                                       + (f"_P{page}" if info.page_count > 1 else ""))
                _write_outputs(out_dir, info, page, lines, "cache")
            else:
                out_dir = os.path.join(out_root, f"P{page:02d}")
                _write_outputs(out_dir, info, page, lines, "cache")
            return "ok", "cache", len(lines), out_dir, ""
    try:
        lines = [{"start": l.start, "end": l.end, "text": l.content}
                 for l in client.get_subtitle_text(info.bvid, page_obj.cid, duration=page_obj.duration,
                                                   lang=lang, title=info.title, desc=info.desc)]
    except Exception as e:
        err = str(e)[:100]
        print(f"  P{page} 字幕获取失败：{err}", file=sys.stderr)
        return "failed", "", 0, "", err
    src = "字幕"
    if not lines:
        if no_whisper:
            return "no_subtitle", src, 0, "", "无字幕且 --no-whisper 跳过转写"
        try:
            import transcriber
            print(f"  P{page} 无字幕，启用 Whisper 转写……", file=sys.stderr)
            cfg = config.load_config()
            def _progress(done, total):
                pct = int(done / total * 100) if total else 0
                if pct % 10 == 0:
                    print(f"    转写进度 {pct}%", file=sys.stderr, end="\r")
            lines = transcriber.transcribe_video(client, info.bvid, page_obj.cid,
                                                 model_size=model_size or "small",
                                                 progress_callback=_progress)
            print("", file=sys.stderr)
            src = "whisper"
        except transcriber.WhisperNotInstalled:
            return "whisper_missing", src, 0, "", "未安装 faster-whisper"
        except Exception as e:
            return "failed", src, 0, "", str(e)[:100]
    if not lines:
        return "failed", src, 0, "", "转写结果为空"
    if single:
        out_dir = os.path.join(out_root, _safe_name(info.owner),  # B3：按 UP 主归档
                               f"{datetime.now():%Y-%m-%d}_{_safe_name(info.title)}"
                               + (f"_P{page}" if info.page_count > 1 else ""))
    else:
        out_dir = os.path.join(out_root, f"P{page:02d}")  # out_root 已含 UP主+合集层
    _write_outputs(out_dir, info, page, lines, src)
    # C3：成功后写缓存（下次命中直接复用）
    if not no_cache:
        import subcache
        subcache.set_cached(info.bvid, page_obj.cid, lines)
    return "ok", src, len(lines), out_dir, ""


def _run_batch(client, info, pages, args, out_root):
    base = os.path.join(os.path.abspath(out_root), _safe_name(info.owner),  # B3：按 UP 主归档
                        f"{datetime.now():%Y-%m-%d}_{_safe_name(info.title)}_合集")
    os.makedirs(base, exist_ok=True)
    results = []
    t_start = time.time()
    elapsed_list = []
    def _write_summary():
        summary = {
            "title": info.title, "owner": info.owner, "bvid": info.bvid, "aid": info.aid,
            "url": f"https://www.bilibili.com/video/{info.bvid}",
            "page_count": info.page_count,
            "batch_fetched_at": datetime.now().isoformat(timespec="seconds"),
            "pages": results,
            "tool": f"bilibili-learn v{VERSION}",
        }
        with open(os.path.join(base, "video_info.json"), "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)
        return summary
    for i, page in enumerate(pages, 1):
        print(f"[{i}/{len(pages)}] 处理 P{page}（{info.page_by_index(page).part[:20]}）……")
        t0 = time.time()
        status, src, n, _, err = _process_page(client, info, page, base, args.no_whisper,
                                               lang=getattr(args, "lang", None),
                                               model_size=getattr(args, "model", None),
                                               no_cache=getattr(args, "no_cache", False))
        elapsed = time.time() - t0
        if i < len(pages):
            time.sleep(getattr(args, "interval", 1.5))  # D3：请求间隔限流
        elapsed_list.append(elapsed)
        avg = sum(elapsed_list) / len(elapsed_list)
        remain_min = avg * (len(pages) - i) / 60
        results.append({"page": page, "part": info.page_by_index(page).part,
                        "status": status, "subtitle_source": src, "line_count": n,
                        "error": err})
        _write_summary()  # 每P后增量写入，中断可续跑
        _LOGGER.info("batch %s P%d %s %s %d行", info.bvid, page, status, src, n)
        print(f"  → {status}" + (f"（{src}，{n} 行）" if n else "") + f"｜耗时 {elapsed:.0f}s，预计剩余 {remain_min:.1f} 分钟")
    summary = _write_summary()
    with open(os.path.join(base, "report_template.md"), "w", encoding="utf-8") as f:
        f.write(render_batch_template(summary))  # C4：批量后自动生成总报告骨架
    _LOGGER.info("batch %s 完成 总耗时%.0fs", info.bvid, time.time() - t_start)
    ok = sum(1 for r in results if r["status"] == "ok")
    print(f"\n批量完成：{ok}/{len(pages)} P 成功")
    print(f"输出目录：{base}")


def render_batch_template(summary: dict) -> str:
    """批量总报告骨架：总览 + 按P分章 + 失败标注"""
    lines = [f"# {summary['title']}", "", "> 批量学习报告（按分P分章）", "",
             "| 项目 | 内容 |", "|------|------|",
             f"| UP主 | {summary['owner']} |",
             f"| BV号 | {summary['bvid']} |",
             f"| 链接 | {summary['url']} |",
             f"| 分P | {summary['page_count']} |", "",
             "## 课程总览", "", "<!-- 读完各 P 字幕后的整体概述 -->", ""]
    for r in summary.get("pages") or []:
        lines += [f"## P{r['page']}：{r['part']}", ""]
        if r["status"] != "ok":
            lines += [f"> ⚠️ 该 P 获取失败（{r['status']}：{r.get('error', '未知')}），建议手动观看", ""]
        else:
            lines += [f"> 字幕来源：{r['subtitle_source']}（{r['line_count']} 行）", "",
                      "<!-- 要点 + 详细解释 + 例子 -->", ""]
    lines += ["## 总术语表", "", "| 概念 | 解释 |", "|------|------|", "",
              "## 学习路径与建议", "", "## 元信息", "",
              f"- 生成时间：{summary.get('batch_fetched_at', '')}",
              f"- 工具：bilibili-learn v{VERSION}", ""]
    return "\n".join(lines)


def _find_report_md(target: str) -> str:
    """查找目录中的报告文件：report.md → <标题>_学习报告.md → report_template.md"""
    for name in ("report.md",):
        p = os.path.join(target, name)
        if os.path.exists(p):
            return p
    import glob
    hits = glob.glob(os.path.join(target, "*_学习报告.md"))
    if hits:
        return hits[0]
    p = os.path.join(target, "report_template.md")
    if os.path.exists(p):
        return p
    return ""


def cmd_report(args):
    """从已有目录（单P/合集）重新生成报告模板，无需重新抓取"""
    target = args.target
    vp = os.path.join(target, "video_info.json")
    if not os.path.exists(vp):
        print(f"目录中没有 video_info.json：{target}", file=sys.stderr)
        sys.exit(1)
    info = json.load(open(vp, encoding="utf-8"))
    if "pages" in info and "batch_fetched_at" in info:
        out = os.path.join(target, "report_template.md")
        open(out, "w", encoding="utf-8").write(render_batch_template(info))
        print(f"已重新生成总报告骨架：{out}")
    else:
        out = os.path.join(target, "report_template.md")
        open(out, "w", encoding="utf-8").write(render_template(info))
        print(f"已重新生成报告模板：{out}")


def cmd_export(args):
    """从报告目录导出 HTML/DOCX"""
    target = args.target
    md_path = _find_report_md(target)
    if not md_path:
        print(f"目录中没有报告文件（report.md / <标题>_学习报告.md / report_template.md）：{target}", file=sys.stderr)
        sys.exit(1)
    md = open(md_path, encoding="utf-8").read()
    from export import md_to_html, export_docx
    name = os.path.splitext(os.path.basename(md_path))[0]
    if args.format == "html":
        out = args.out or os.path.join(target, name + ".html")
        open(out, "w", encoding="utf-8").write(md_to_html(md))
    else:
        out = args.out or os.path.join(target, name + ".docx")
        export_docx(md, out)
    print(f"已导出：{out}")


def cmd_favs(args, client: ApiClient = None):
    """列出当前账号的收藏夹"""
    client = client or ApiClient()
    try:
        folders = client.get_fav_folders()
    except BiliError as e:
        print(f"获取收藏夹失败：{e}（需要有效登录 cookie）", file=sys.stderr)
        sys.exit(2)
    if not folders:
        print("没有收藏夹", file=sys.stderr)
        sys.exit(1)
    for i, f in enumerate(folders, 1):
        print(f"{i}. [{f['id']}] {f['title']}（{f['media_count']}个视频）")


def _resolve_fav(client: ApiClient, fav: str, pick: int):
    """解析 --fav：按 id 或名称匹配收藏夹，返回 (bvid, title)"""
    folders = client.get_fav_folders()
    folder = None
    if fav.isdigit():
        folder = next((f for f in folders if str(f["id"]) == fav), None)
    if folder is None:
        folder = next((f for f in folders if fav in f["title"]), None)
    if folder is None:
        raise BiliError(-400, "未找到收藏夹：" + fav + "（现有：" + '，'.join(f['title'] for f in folders) + "）")
    medias = client.get_fav_medias(folder["id"], pn=1, ps=20)
    if pick > 20:  # 超出第一页：拉后续页
        medias += client.get_fav_medias(folder["id"], pn=2, ps=20)
    if pick > 40:
        medias += client.get_fav_medias(folder["id"], pn=3, ps=20)
    if not medias:
        raise BiliError(-400, f"收藏夹「{folder['title']}」为空")
    idx = pick - 1
    if idx < 0 or idx >= len(medias):
        raise BiliError(-400, f"收藏夹第 {pick} 个视频不存在（共 {len(medias)} 个）")
    m = medias[idx]
    print(f"收藏夹「{folder['title']}」第{pick}个：{m['title']}（{m['bvid']}）", file=sys.stderr)
    return m["bvid"], m["title"]


def cmd_run(args, client: ApiClient = None):
    client = client or ApiClient()
    cfg = config.load_config()
    config.apply_hf_env(cfg)
    out_root = args.out if getattr(args, "out", None) else (cfg.get("out_dir") or "output")
    try:
        if client.check_login() is False:
            print("⚠ 登录 cookie 已过期或未配置：搜索与 AI 字幕将不可用，仅可获取无字幕视频并走 Whisper 转写。", file=sys.stderr)
            print("  请更新 scripts/.bili_cookie（SESSDATA=xxx）或设置环境变量 BILI_COOKIE", file=sys.stderr)
    except Exception:
        pass  # 检测失败不阻断主流程
    if getattr(args, "fav", None):
        try:
            bvid, _t = _resolve_fav(client, args.fav, args.pick)
            spec = resolve_input(bvid)
        except BiliError as e:
            print(f"收藏夹解析失败：{e}", file=sys.stderr)
            sys.exit(2)
    else:
        try:
            spec = resolve_input(args.input)
        except SearchNeeded:
            try:
                results = client.search(args.input, limit=max(1, args.pick))
            except BiliError as e:
                print(f"搜索失败：{e}（可能是风控，建议改用链接）", file=sys.stderr)
                sys.exit(2)
            if not results:
                print(f"未找到与「{args.input}」相关的视频", file=sys.stderr)
                sys.exit(1)
            hit = results[args.pick - 1]
            print(f"名称搜索命中：{hit['title']}（{hit['bvid']}）")
            spec = resolve_input(hit["bvid"])
    try:
        info = client.get_video_info(bvid=spec.bvid, aid=spec.aid)
        try:
            pages = _select_pages(args, info)
        except ValueError as e:
            print(f"参数错误：{e}", file=sys.stderr)
            sys.exit(2)
        if getattr(args, "resume", False) and len(pages) > 1:
            skip = _load_resume_pages(out_root, info.bvid)
            if skip:
                pages = [p for p in pages if p not in skip]
                print(f"续跑：跳过已完成 {len(skip)} 个 P（{sorted(skip)}），剩余 {len(pages)} 个", file=sys.stderr)
            if not pages:
                print("续跑：全部 P 均已完成，无需处理", file=sys.stderr)
                return
        if len(pages) == 1 and not getattr(args, "pages", None) and not getattr(args, "all_", False):
            status, src, n, out_dir, err = _process_page(client, info, pages[0], os.path.abspath(out_root),
                                                         args.no_whisper, single=True,
                                                         lang=getattr(args, "lang", None),
                                                         model_size=getattr(args, "model", None),
                                                         no_cache=getattr(args, "no_cache", False))
            if status != "ok":
                msgs = {"no_subtitle": "该视频无字幕（--no-whisper 已跳过转写）",
                        "failed": "获取失败",
                        "whisper_missing": "未安装 faster-whisper"}
                print(msgs[status], file=sys.stderr)
                if status == "whisper_missing":
                    print("可选：1) pip install faster-whisper 后重试；2) 换一个带字幕的视频；3) --no-whisper 跳过", file=sys.stderr)
                _LOGGER.info("single %s P%d %s %s", info.bvid, pages[0], status, err)
                sys.exit({"no_subtitle": 3, "failed": 4, "whisper_missing": 5}[status])
            _LOGGER.info("single %s P%d ok %s %d行", info.bvid, pages[0], src, n)
            print(f"\n完成！输出目录：{out_dir}")
            print("已生成：video_info.json / subtitle.txt / report_template.md")
        else:
            _run_batch(client, info, pages, args, out_root)
    except BiliError as e:
        print(f"获取失败：{e}", file=sys.stderr)
        sys.exit(4)

def main(argv=None):
    setup_logging()
    config.ensure_config()
    p = argparse.ArgumentParser(prog="bili", description="bilibili 学习视频内容获取")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("resolve", help="解析输入为 bvid/page")
    r.add_argument("input"); r.set_defaults(func=cmd_resolve)
    s = sub.add_parser("search", help="搜索视频候选")
    s.add_argument("keyword"); s.add_argument("--limit", type=int, default=5)
    s.set_defaults(func=cmd_search)
    fv = sub.add_parser("favs", help="列出账号收藏夹")
    fv.set_defaults(func=cmd_favs)
    fs = sub.add_parser("favs-scan", help="扫描收藏夹：拉全量+主题过滤+优先级排序+快照")
    fs.add_argument("fav", help="收藏夹 id 或名称")
    fs.add_argument("--filter", default=None, help="关键词列表（逗号分隔，默认内置 AI 关键词）")
    fs.add_argument("--priority", action="store_true", help="按播放量降序输出建议处理顺序")
    fs.add_argument("--out", default=None, help="快照输出目录")
    fs.add_argument("--snapshot", action="store_true", help="保存快照 JSON（供增量对比）")
    fs.set_defaults(func=lambda a: _cmd_favs_scan(a))
    def _cmd_favs_scan(args):
        import favs
        return favs.cmd_favs_scan(args)
    def _cmd_doctor(args):
        import doctor
        return doctor.main()
    doc = sub.add_parser("doctor", help="环境自检（cookie/GPU/whisper/模型缓存）")
    doc.set_defaults(func=_cmd_doctor)
    run = sub.add_parser("run", help="获取视频内容（字幕/转写）并落盘")
    run.add_argument("input", nargs="?", default=None, help="视频链接/BV/av/名称（--fav 模式可省略）")
    g = run.add_mutually_exclusive_group()
    g.add_argument("--page", type=int, default=None, help="单分P页码（默认 1；与 --pages/--all 互斥）")
    g.add_argument("--pages", default=None, help='批量分P范围，如 "1-10" 或 "1,3,5-8"')
    g.add_argument("--all", dest="all_", action="store_true", help="批量处理全部分P")
    run.add_argument("--fav", default=None, help='从收藏夹选视频（收藏夹id或名称，配合 --pick 选第N个）')
    run.add_argument("--resume", action="store_true", help="跳过已成功处理的P（断点续跑）")
    run.add_argument("--lang", default=None, help='字幕语言（zh/ja/en等，默认自动中文优先）')
    run.add_argument("--model", default=None, help="whisper 模型（tiny/small/medium/large-v3，默认 config.whisper_model）")
    run.add_argument("--out", default=None, help="输出根目录（默认 config.out_dir 或 output）")
    run.add_argument("--no-whisper", action="store_true", help="无字幕时不转写直接失败")
    run.add_argument("--no-cache", dest="no_cache", action="store_true", help="不使用字幕/转写缓存（重新获取）")
    run.add_argument("--interval", type=float, default=1.5, help="批量分P之间的请求间隔秒数（默认 1.5，防接口限流）")
    run.add_argument("--pick", type=int, default=1, help="名称搜索时选第 N 个候选")
    run.set_defaults(func=cmd_run)
    def _cmd_merge(args):
        import mergeutil
        return mergeutil.cmd_merge(args)
    mg = sub.add_parser("merge", help="合并两个合集目录（复制缺失的 P 子目录，不覆盖已有）")
    mg.add_argument("src", help="源合集目录")
    mg.add_argument("dst", help="目标合集目录")
    mg.set_defaults(func=_cmd_merge)
    rep = sub.add_parser("report", help="从已有目录（单P/合集）重新生成报告骨架")
    rep.add_argument("target", help="单P或合集目录")
    rep.set_defaults(func=cmd_report)
    ex = sub.add_parser("export", help="导出报告为 HTML/DOCX")
    ex.add_argument("target", help="含 report.md 的目录")
    ex.add_argument("--format", choices=["html", "docx"], default="html")
    ex.add_argument("--out", default=None, help="输出文件路径（默认在报告目录）")
    ex.set_defaults(func=cmd_export)
    args = p.parse_args(argv)
    args.func(args)

if __name__ == "__main__":
    main()
