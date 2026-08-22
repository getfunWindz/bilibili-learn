# -*- coding: utf-8 -*-
"""C1: bili doctor 环境自检
诊断 cookie / GPU(cuBLAS·cuDNN) / faster-whisper 版本兼容 / 模型缓存 / 输出目录，
输出 ✅/❌ 与修复建议，避免每次环境问题靠人工多轮排查。
"""
import os
import sys
import importlib.util


def check_cookie(client=None) -> bool:
    """cookie 有效性：nav 接口正常即有效；异常视为无效"""
    try:
        if client is None:
            from api_client import ApiClient
            client = ApiClient()
        return client.check_login()
    except Exception:
        return False


def check_gpu() -> dict:
    """GPU 可用性：ctranslate2 设备 + cuBLAS/cuDNN DLL 探测（缺失时给出 pip 修复命令）"""
    from transcriber import _ensure_nvidia_paths, detect_device
    _ensure_nvidia_paths()
    out = {"device": "cpu", "cublas_ok": False, "cudnn_ok": False,
           "hint": "CPU 模式可用（较慢）；安装运行库可启用 GPU：pip install nvidia-cublas-cu12 nvidia-cudnn-cu12"}
    try:
        device = detect_device()
        out["device"] = device
        if device != "cuda":
            return out
        import ctypes.util
        cublas = ctypes.util.find_library("cublas64_12") or (
            _dll_in_site_packages("cublas64_12.dll"))
        cudnn = ctypes.util.find_library("cudnn64_9") or (
            _dll_in_site_packages("cudnn64_9.dll"))
        out["cublas_ok"] = bool(cublas)
        out["cudnn_ok"] = bool(cudnn)
        if not (out["cublas_ok"] and out["cudnn_ok"]):
            out["hint"] = ("GPU 可用但缺少运行库 DLL："
                           "pip install nvidia-cublas-cu12 nvidia-cudnn-cu12")
        else:
            out["hint"] = "GPU 转写就绪"
    except Exception as e:
        out["hint"] = f"GPU 检查异常：{e}"
    return out


def _dll_in_site_packages(dll_name: str) -> str:
    import site
    for sp in site.getsitepackages():
        for root, _dirs, files in os.walk(os.path.join(sp, "nvidia")):
            if dll_name in files:
                return os.path.join(root, dll_name)
    return ""


def check_whisper() -> dict:
    """faster-whisper 安装与 API 兼容（>=1.2 用 log_progress，旧版用 progress_callback）"""
    out = {"installed": False, "compatible": False, "version": "", "hint": ""}
    if importlib.util.find_spec("faster_whisper") is None:
        out["hint"] = "未安装 faster-whisper：pip install faster-whisper"
        return out
    import inspect
    from faster_whisper import WhisperModel
    out["installed"] = True
    try:
        from faster_whisper import __version__ as v
        out["version"] = v
    except Exception:
        out["version"] = "unknown"
    params = inspect.signature(WhisperModel.transcribe).parameters
    out["compatible"] = "log_progress" in params or "progress_callback" in params
    out["hint"] = "OK" if out["compatible"] else "API 不兼容：请升级 faster-whisper"
    return out


def check_models() -> dict:
    """Whisper 模型缓存（huggingface hub）"""
    cache = os.path.expanduser(r"~\.cache\huggingface\hub")
    models = [d for d in os.listdir(cache) if d.startswith("models--Systran--faster-whisper")]
    return {"models": [m.replace("models--Systran--faster-whisper-", "") for m in models],
            "cache_dir": cache}


def check_out_dir(path: str = "") -> dict:
    """输出目录可写性"""
    path = path or os.getcwd()
    try:
        os.makedirs(path, exist_ok=True)
        test = os.path.join(path, ".write_test")
        open(test, "w").close()
        os.remove(test)
        return {"ok": True, "path": path}
    except Exception as e:
        return {"ok": False, "path": path, "error": str(e)}


def main(argv=None) -> int:
    """CLI 入口。bili.py 子命令 doctor 调用本函数。"""
    import api_client  # noqa: F401  确保 api_client 可导入（cookie 检查用）
    args = sys.argv[1:] if argv is None else argv
    print("=== bilibili-learn 环境自检 ===")
    # cookie
    ok = check_cookie()
    print(f"{'✅' if ok else '❌'} cookie 登录态：{'有效' if ok else '无效/未配置（检查 scripts/.bili_cookie 或环境变量 BILI_COOKIE）'}")
    # GPU
    g = check_gpu()
    gpu_flag = "✅" if (g["device"] == "cuda" and g["cublas_ok"] and g["cudnn_ok"]) else "❌"
    print(f"{gpu_flag} GPU：设备={g['device']} cuBLAS={'✓' if g['cublas_ok'] else '✗'} cuDNN={'✓' if g['cudnn_ok'] else '✗'}")
    print(f"     {g['hint']}")
    # whisper
    w = check_whisper()
    print(f"{'✅' if w['installed'] and w['compatible'] else '❌'} faster-whisper："
          f"{'v'+w['version'] if w['version'] else '未安装'} 兼容={'✓' if w['compatible'] else '✗'} {w['hint']}")
    # 模型缓存
    m = check_models()
    if m["models"]:
        print(f"✅ 模型缓存：{', '.join(m['models'])}（{m['cache_dir']}）")
    else:
        print(f"⚠️ 模型缓存为空：首次转写将自动下载（国内网络可设 HF_ENDPOINT=https://hf-mirror.com）")
    # 输出目录
    o = check_out_dir()
    print(f"{'✅' if o['ok'] else '❌'} 输出目录：{o['path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
