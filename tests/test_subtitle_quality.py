# -*- coding: utf-8 -*-
"""B1/B2: 字幕质量校验测试——内容错乱检测 + 单 P 时长校验回归"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from api_client import ApiClient, SubtitleLine

# ---------- 测试样本 ----------

def _lines(texts):
    """构造 SubtitleLine 列表（每行 2 秒）"""
    out = []
    t = 0.0
    for i, txt in enumerate(texts):
        out.append(SubtitleLine(t, t + 2.0, txt))
        t += 2.0
    return out

# 正常样本：技术讲解，标题关键词出现在字幕中
NORMAL_TEXTS = [
    "KV Cache 的核心是缓存注意力计算中的 K 和 V 矩阵",
    "推理阶段每一层的 Key 和 Value 都需要保存",
    "这样可以避免重复计算，加速 token 生成速度",
    "KV Cache 会占用大量显存，需要量化或 GQA 优化",
    "多头注意力机制中每个头都有独立的 QKV 权重",
] * 40  # 400 行 ≈ 800s

# 错乱样本 1：标题是 KV Cache，内容却是靶场打枪（本次真实事故）
RANGE_TEXTS = [
    "兄弟们你们知道咱们中国人在美国德州开的靶场是什么样子吗",
    "这个靶场里的枪店武器五花八门，比美国人的枪店还要牛逼",
    "这个是全自动的 AUG，这个是 URGI 还带消音器的",
    "老李今天特意飞到了德州，来带你们看看全美国最牛逼的国人靶场",
    "这个 M2 重机枪要七十斤，我是扛不住的",
] * 50  # 250 行

# 错乱样本 2：影视剧台词（本次真实事故）
DRAMA_TEXTS = [
    "你还是人吗我的好夫人，等会就到你了",
    "男生娶你不过是因为你家庭富裕，如今顾家已是半斤首富",
    "出狱后的他只想做一件事，那就是找到妹妹",
    "但是甲士官提醒他，不能碰",
    "碧瑶你这个病占有我啊啊",
] * 30  # 150 行

# 错乱样本 3：歌词
LYRIC_TEXTS = ["♪ Cause you could all in here with the sun", "♪ Stay like we caught in our eyes"] * 40

# 越界样本：末条时间远超单 P 时长（3018 行/10109s vs 单 P 615s，本次真实事故）
OVERRUN_TEXTS = DRAMA_TEXTS * 60  # 300 行，末条约 600s > 短 P 时长

TITLE_KV = "【8】KV Cache 原理讲解"
TITLE_TRANSFORMER = "2.2、Transformer模型的整体架构"


# ---------- B1: 内容错乱检测 ----------

def test_normal_subtitle_plausible():
    lines = _lines(NORMAL_TEXTS)
    ok, reason = ApiClient._subtitle_plausible(lines, TITLE_KV, "", 800)
    assert ok, f"正常字幕被误判：{reason}"


def test_range_subtitle_suspicious():
    """靶场内容 + KV Cache 标题 → 可疑（标题关键词命中率过低）"""
    lines = _lines(RANGE_TEXTS)
    ok, reason = ApiClient._subtitle_plausible(lines, TITLE_KV, "", 500)
    assert not ok, "靶场错乱字幕未被识别"
    assert "零命中" in reason


def test_drama_subtitle_suspicious():
    lines = _lines(DRAMA_TEXTS)
    ok, reason = ApiClient._subtitle_plausible(lines, TITLE_TRANSFORMER, "", 300)
    assert not ok, "影视剧错乱字幕未被识别"


def test_lyric_subtitle_suspicious():
    lines = _lines(LYRIC_TEXTS)
    ok, reason = ApiClient._subtitle_plausible(lines, TITLE_KV, "", 160)
    assert not ok, "歌词字幕未被识别"
    assert "歌词" in reason


def test_overrun_subtitle_suspicious():
    """末条时间远超单 P 时长（3018 行/10109s 案例的形态）"""
    lines = _lines(OVERRUN_TEXTS)
    ok, reason = ApiClient._subtitle_plausible(lines, TITLE_TRANSFORMER, "", 615)
    assert not ok, "越界字幕未被识别"


# ---------- B2: 单 P 时长校验（回归：曾误用总时长漏检） ----------

def test_complete_with_single_page_duration():
    """末条超出单 P 时长 130% → 必须判不可信（10109s vs 615s 案例）"""
    lines = _lines(DRAMA_TEXTS * 60)
    # 末条约 600s < 615*0.6=369? 构造明确越界：手动造末条超长
    from api_client import SubtitleLine as SL
    fake = [SL(0, 10109.0, "越界字幕")]
    assert not ApiClient._subtitle_complete(fake, 615), "越界字幕应判不可信"
    # 正常边界：末条 610s ∈ [615*0.6, 615*1.3] → 可信
    ok_lines = [SL(0, 610.0, "正常结尾")]
    assert ApiClient._subtitle_complete(ok_lines + _lines(NORMAL_TEXTS), 615)
