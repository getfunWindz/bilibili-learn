# -*- coding: utf-8 -*-
"""A0/B3: 名词注释与报告覆盖校验
- 术语库管理：load/save/merge（agent 写报告时自动沉淀新术语）
- 报告校验：check_coverage（字幕出现但报告未提及）与 check_annotation（提及但未注释）
"""
import json
import os
import re

_REFERENCES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "references")
DEFAULT_PATH = os.path.join(_REFERENCES_DIR, "glossary.json")

# 报告注释小节标记：每个知识点末尾的「名词注释」小节
ANNOTATION_HEADER = "**名词注释**"


def load_glossary(path: str = DEFAULT_PATH) -> dict:
    """读取术语库 → {术语: 一句话解释}；不存在返回 {}"""
    if not os.path.exists(path):
        return {}
    try:
        return json.load(open(path, encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_glossary(glossary: dict, path: str = DEFAULT_PATH) -> None:
    """写回术语库（自动沉淀）"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(glossary, f, ensure_ascii=False, indent=2)


def merge_terms(glossary: dict, new_terms: dict) -> dict:
    """自动沉淀：新术语追加；已有术语保留原解释（不覆盖）"""
    merged = dict(glossary)
    for k, v in new_terms.items():
        if k not in merged and v:
            merged[k] = v
    return merged


def _term_in_subtitle(term: str, subtitle_text: str) -> bool:
    """术语是否出现在字幕中（大小写不敏感，容错空格）"""
    t = term.strip().lower()
    s = subtitle_text.lower()
    return t in s


def check_coverage(report_text: str, subtitle_text: str, glossary: dict = None) -> list:
    """B3 覆盖自检：字幕中出现、但报告全文（正文+注释）完全未提及的术语"""
    glossary = glossary if glossary is not None else load_glossary()
    missing = []
    for term in glossary:
        if not _term_in_subtitle(term, subtitle_text):
            continue
        if not re.search(re.escape(term), report_text, re.IGNORECASE):
            missing.append(term)
    return missing


def _annotation_sections(report_text: str) -> str:
    """提取报告中所有「名词注释」小节的内容（拼接为一个文本）"""
    parts = re.split(r"(?m)^\*\*名词注释\*\*", report_text)
    if len(parts) < 2:
        return ""
    chunks = []
    for part in parts[1:]:
        # 每个小节到下一个 ## 或 段落空行处结束（保守取前 500 字符）
        section = part.split("\n##")[0][:500]
        chunks.append(section)
    return "\n".join(chunks)


def check_annotation(report_text: str, subtitle_text: str, glossary: dict = None) -> list:
    """A0 注释校验：字幕中出现且报告正文提及、但未在「名词注释」小节注释的术语"""
    glossary = glossary if glossary is not None else load_glossary()
    annotations = _annotation_sections(report_text)
    unannotated = []
    for term in glossary:
        if not _term_in_subtitle(term, subtitle_text):
            continue
        if re.search(re.escape(term), report_text, re.IGNORECASE) and \
           not re.search(re.escape(term), annotations, re.IGNORECASE):
            unannotated.append(term)
    return unannotated


def main(argv=None):
    """CLI：
    glossary.py list              列出术语库
    glossary.py add <术语> <解释>  自动沉淀单个术语（已有则保留原解释）
    glossary.py check <报告.md> <subtitle.txt>   注释/覆盖校验（R11/B3）
    """
    import sys
    args = sys.argv[1:] if argv is None else argv
    if not args:
        print(__doc__)
        print("用法：list | add <术语> <解释> | check <报告.md> <subtitle.txt>")
        return 1
    cmd = args[0]
    if cmd == "list":
        for k, v in load_glossary().items():
            print(f"{k}: {v}")
        return 0
    if cmd == "add" and len(args) >= 3:
        term, expl = args[1], " ".join(args[2:])
        g = load_glossary()
        merged = merge_terms(g, {term: expl})
        save_glossary(merged)
        print(f"已沉淀：{term} → {expl}")
        return 0
    if cmd == "check" and len(args) >= 3:
        report_text = open(args[1], encoding="utf-8").read()
        subtitle_text = open(args[2], encoding="utf-8").read()
        unannotated = check_annotation(report_text, subtitle_text)
        uncovered = check_coverage(report_text, subtitle_text)
        print("== 注释缺失（正文提及但未注释）==")
        for t in unannotated:
            print(f"  - {t}")
        print("== 覆盖遗漏（字幕出现但报告未提及）==")
        for t in uncovered:
            print(f"  - {t}")
        if not unannotated and not uncovered:
            print("✅ 无遗漏")
        return 0 if not unannotated and not uncovered else 2
    print(f"未知命令：{cmd}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
