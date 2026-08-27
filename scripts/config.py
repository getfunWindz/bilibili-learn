"""配置文件：scripts/config.json（输出目录 / whisper 模型 / HF 镜像）"""
import json
import os

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

DEFAULT_CONFIG = {
    "out_dir": "",              # 默认输出根目录（--out 未指定时使用）
    "whisper_model": "small",   # whisper 模型大小：tiny/small/medium/large-v3
    "hf_endpoint": "",          # HF 镜像（如 https://hf-mirror.com），空则用官方
    "hf_disable_xet": True,     # 禁用 HF xet 协议（镜像站必需）
    "vision": {                 # 第三条路径：多模态大模型 API（OpenAI 兼容）
        "enabled": False,       # 是否启用（启用后无人声视频可询问走视觉转写）
        "base_url": "",         # OpenAI 兼容端点，如 https://api.openai.com/v1
        "api_key": "",          # API 密钥（config.json 已被 gitignore，不会泄露）
        "model": "gpt-4o-mini", # 多模态模型名（如 qwen-vl-max / gemini-2.0-flash）
        "frame_interval": 10,   # 抽帧间隔（秒）
        "max_frames": 24,       # 最多帧数（超出均匀抽样）
        "prompt": ""            # 自定义视觉转写提示词（空则用默认）
    },
}

def load_config() -> dict:
    """读取配置；文件不存在返回默认。缺失字段用默认值补齐"""
    cfg = dict(DEFAULT_CONFIG)
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                user = json.load(f)
            cfg.update({k: v for k, v in user.items() if k in DEFAULT_CONFIG})
            # vision 块：缺失字段用默认补齐
            if isinstance(cfg.get("vision"), dict):
                base = dict(DEFAULT_CONFIG["vision"])
                base.update({k: v for k, v in cfg["vision"].items() if k in base})
                cfg["vision"] = base
        except Exception:
            pass  # 配置损坏时静默用默认
    return cfg

def ensure_config() -> str:
    """不存在时写入默认配置，返回路径"""
    if not os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_CONFIG, f, ensure_ascii=False, indent=2)
    return CONFIG_PATH

def apply_hf_env(cfg: dict) -> None:
    """按配置设置 HF 环境变量（setdefault：不覆盖用户已有环境变量）"""
    if cfg.get("hf_endpoint"):
        os.environ.setdefault("HF_ENDPOINT", cfg["hf_endpoint"])
    if cfg.get("hf_disable_xet"):
        os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
