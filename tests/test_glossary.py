# -*- coding: utf-8 -*-
"""A0: 名词注释机制测试——术语库读写/自动沉淀/注释与覆盖校验"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from glossary import load_glossary, save_glossary, merge_terms, check_coverage, check_annotation

SEED = {
    "KV Cache": "键值缓存：推理时缓存注意力 K/V 矩阵、避免重复计算的技术",
    "MHA": "多头注意力：多组独立注意力并行后拼接融合",
    "softmax": "将得分归一化为概率分布的激活函数",
}


def _tmp_glossary_path():
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    return path


def test_load_glossary_empty_when_missing():
    assert load_glossary("/nonexistent/glossary.json") == {}


def test_save_load_roundtrip():
    p = _tmp_glossary_path()
    try:
        save_glossary(SEED, p)
        assert load_glossary(p) == SEED
    finally:
        os.remove(p)


def test_merge_terms_appends_new_keeps_existing():
    new = {"KV Cache": "被覆盖的解释不应生效", "GQA": "分组查询注意力：头分组共享 KV"}
    merged = merge_terms(SEED, new)
    assert merged["KV Cache"] == SEED["KV Cache"], "已有术语解释不得被覆盖"
    assert merged["GQA"] == "分组查询注意力：头分组共享 KV"


def test_check_coverage_finds_missing_terms():
    report = "本报告讲解了 KV Cache 的原理与 softmax 的应用。"
    subtitle = "KV Cache 是核心概念，MHA 也很重要，softmax 用于归一化。"
    missing = check_coverage(report, subtitle, SEED)
    assert "MHA" in missing, "字幕出现但报告完全未提及的术语应被报告"


def test_check_annotation_finds_unannotated_terms():
    # 报告正文提及了 MHA 但知识点末尾没有它的注释 → 注释缺失
    report = "MHA 是重要的注意力机制。\n\n**名词注释**：\n- KV Cache：键值缓存\n- softmax：归一化函数"
    subtitle = "MHA 与 KV Cache 都是推理优化的关键。"
    unannotated = check_annotation(report, subtitle, SEED)
    assert "MHA" in unannotated, "正文提及但注释小节缺失的术语应被报告"
    assert "KV Cache" not in unannotated, "已注释术语不应误报"


def test_check_annotation_clean_report():
    report = "KV Cache 是核心。\n\n**名词注释**：\n- KV Cache：键值缓存"
    subtitle = "KV Cache 是核心。"
    assert check_annotation(report, subtitle, SEED) == []
