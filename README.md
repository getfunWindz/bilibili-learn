# bilibili-learn

将 bilibili 学习视频总结为中文详解报告（Markdown），保存到百度同步盘，多设备可见。

## 功能

- 输入：视频**链接 / BV号 / av号 / 名称 / UP主**
- 获取：字幕优先（官方接口，含 AI 字幕），无字幕时 faster-whisper 本地转写兜底（CPU/GPU 自适应，无需系统 ffmpeg）
- 输出：`video_info.json`（元信息）+ `subtitle.txt`（字幕全文）+ `report_template.md`（报告骨架）
- 报告正文由 agent 撰写，交付到 `C:\data\BaiduSyncdisk\bilibili学习笔记\YYYY-MM-DD_<标题>\`

## 命令

```bash
python scripts/bili.py resolve <输入>              # 解析链接/BV/av → bvid/page
python scripts/bili.py search <关键词> [--limit N]  # 名称/UP主 → 候选列表
python scripts/bili.py run <输入> [--page N] --out <目录> [--no-whisper] [--pick N]
```

## 依赖

```bash
pip install -r requirements.txt   # faster-whisper 可延后按需安装
```

## 国内网络说明

- Whisper 模型首次下载需走 HF 镜像：`HF_ENDPOINT=https://hf-mirror.com HF_HUB_DISABLE_XET=1 python scripts/bili.py ...`
- 登录 cookie（搜索/AI 字幕需要）：把 `SESSDATA=xxx` 写入 `scripts/.bili_cookie`（已 gitignore）或设置环境变量 `BILI_COOKIE`

## 开发

```bash
python -m pytest tests/ -v
```
