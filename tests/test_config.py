import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import pytest
import config as cfg

def test_default_config(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "CONFIG_PATH", str(tmp_path / "none.json"))
    c = cfg.load_config()
    assert c["whisper_model"] == "small"
    assert c["out_dir"] == ""
    assert c["hf_endpoint"] == ""
    assert c["hf_disable_xet"] is True

def test_load_custom_config(tmp_path, monkeypatch):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"out_dir": "D:/笔记", "whisper_model": "medium"}), encoding="utf-8")
    monkeypatch.setattr(cfg, "CONFIG_PATH", str(p))
    c = cfg.load_config()
    assert c["out_dir"] == "D:/笔记" and c["whisper_model"] == "medium"
    assert c["hf_endpoint"] == ""  # 缺失字段用默认

def test_ensure_creates_default(tmp_path, monkeypatch):
    p = tmp_path / "config.json"
    monkeypatch.setattr(cfg, "CONFIG_PATH", str(p))
    path = cfg.ensure_config()
    assert os.path.exists(path)
    c = json.load(open(path, encoding="utf-8"))
    assert c["whisper_model"] == "small"

def test_apply_hf_env(tmp_path, monkeypatch):
    monkeypatch.delenv("HF_ENDPOINT", raising=False)
    cfg.apply_hf_env({"hf_endpoint": "https://hf-mirror.com", "hf_disable_xet": True})
    assert os.environ.get("HF_ENDPOINT") == "https://hf-mirror.com"
    assert os.environ.get("HF_HUB_DISABLE_XET") == "1"
    # 已有环境变量不被覆盖（setdefault 语义）
    monkeypatch.setenv("HF_ENDPOINT", "https://custom")
    cfg.apply_hf_env({"hf_endpoint": "https://other", "hf_disable_xet": True})
    assert os.environ.get("HF_ENDPOINT") == "https://custom"
