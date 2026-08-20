---
name: bilibili-learn
description: 将 bilibili 视频总结为中文学习报告。触发词：「总结这个B站视频」「bilibili视频总结」「把视频整理成学习笔记」「根据视频链接/名称/UP主生成学习报告」等，用户提供 bilibili 链接/BV号/av号/视频名称/UP主名。
---

# bilibili-learn：B 站学习视频总结

## 职责
定位用户指定的 bilibili 视频 → 提取内容（字幕优先，Whisper 兜底）→ 撰写**中文详解学习报告** → 保存到百度同步盘。

## 工作流

### 0. 配置（config.json，可选）
首次运行自动生成 `scripts/config.json`：`out_dir`（默认输出目录）/ `whisper_model`（tiny/small/medium）/ `hf_endpoint`（HF 镜像）/ `hf_disable_xet`。CLI 参数优先于配置。

### 1. 定位视频
- 链接 / BV号 / av号：运行 `python scripts/bili.py resolve <输入>` 确认解析无误
- 名称 / UP主：运行 `python scripts/bili.py search <关键词>` 列出候选，**展示给用户确认**后再进行下一步

### 2. 获取内容
```bash
python scripts/bili.py run <输入> [--page N] --out <临时工作目录>
```
- 有字幕：直接落盘字幕文本（自动两次一致性 + 时长覆盖校验，不可信时降级 Whisper）
- 无字幕：自动 Whisper 转写（首次需 `pip install faster-whisper`，CPU int8，无需显卡）
- **国内网络首次下载模型**：设置环境变量后运行 `HF_ENDPOINT=https://hf-mirror.com HF_HUB_DISABLE_XET=1 python scripts/bili.py ...`（HF 直连超时；新版 hub 需禁用 xet 协议）
- 输出 3 个文件：`video_info.json`（元信息）、`subtitle.txt`（字幕全文）、`report_template.md`（报告骨架）

### 3. 撰写报告（agent 核心工作）
读取上述 3 个文件，按骨架补全：
1. **一句话总结**
2. **分节详解**：按视频内容自然分段，每节 = 要点 + 详细解释 + 例子（字幕较长时分段多次读完再写，保证覆盖全部内容）
3. **术语表**：视频中的专业概念逐条通俗解释
4. **金句与重要观点**、**原话摘录**（从字幕中挑选 5~10 条有教学价值/启发性的原话，附时间点）、**学习收获与行动建议**

### 4. 交付
保存为 `C:\data\BaiduSyncdisk\bilibili学习笔记\<UP主>\YYYY-MM-DD_<标题>\report.md`（按 UP 主归档，与脚本输出目录结构一致），并向用户汇报报告摘要。

### 5. 多 P 合集
- 默认第 1 P；链接带 `?p=N` 自动跟随；用户指定时用 `--page N`
- **批量模式（v2.0）**：`--all` 或 `--pages "1-10" / "1,3,5-8"`（与 --page 互斥）
  - 输出：`<日期>_<标题>_合集/` 下每 P 一个子目录（P01/P02…，含 subtitle.txt）+ 汇总 `video_info.json`（各 P 处理状态+失败原因）
  - 失败不中断：某 P 无字幕/获取失败会标记（no_subtitle / failed / whisper_missing），其余继续，报告中注明
  - **断点续跑（v2.1）**：`--resume` 跳过已成功 P（汇总增量写入，中断后可直接续跑）
  - **进度显示（v2.1）**：每 P 耗时 + 预计剩余分钟；whisper 转写有百分比进度
- **报告再生成（v2.1）**：`bili report <目录>` 从已有字幕重新生成报告骨架（改模板后无需重抓）
- **报告导出（C2）**：`bili export <目录> --format html|docx`（Markdown → HTML/DOCX）
- **多语言字幕（C1）**：`--lang ja/en` 选择指定语言字幕（默认自动中文优先）
- **视频信息增强（C3）**：报告元信息含简介/播放/点赞/收藏
- **运行日志（v2.1）**：每次运行记录到 scripts/logs/bili.log

### 6. 相关性分析与报告组织（agent 核心工作）
读取汇总 video_info.json 与各 P 字幕，先做**相关性聚类**：

**判断信号**（满足其一即强相关）：
1. 主题连贯或递进（如"上节课我们讲了…"互相引用）
2. 系列化命名（P1 基础 / P2 进阶 / P3 实战）
3. 同一主题的不同角度（原理 / 代码 / 案例）
（UP 主相同不是充分条件）

**分组输出规则**：
- 相关的一组 → 生成一份**总报告** `report.md`（存合集目录）：总览 → **按 P 分章**（每章 = 该 P 的详解：要点+解释+例子）→ 总术语表 → 学习路径 → 元信息
- 不相关的 P → 各自独立报告（沿用单 P 完整结构），命名 `report_P{n}.md`
- 混合合集 → 拆成多组，每组一份报告（`report_P1-P5.md`、`report_P6-P8.md`）
- 失败 P（failed 等）→ 在报告中注明"该 P 获取失败，建议手动观看"

## 注意事项
- 搜索接口偶发风控：失败时提示用户改用链接
- 报告必须中文、详细、带解释；宁可长不可略
