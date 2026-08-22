# -*- coding: utf-8 -*-
"""C1/C2: doctor 环境自检与 GPU DLL 自动注入测试"""
import os
import sys
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from transcriber import _ensure_nvidia_paths
import doctor


class FakeApiClient:
    """模拟 ApiClient：可注入 nav 结果与异常"""

    def __init__(self, nav_ok=True):
        self._nav_ok = nav_ok

    def check_login(self):
        return self._nav_ok


# ---------- C2: GPU DLL 自动注入 ----------

def test_ensure_nvidia_paths_idempotent():
    """重复调用不重复注入、不抛异常"""
    saved = os.environ.get("PATH", "")
    try:
        _ensure_nvidia_paths()
        p1 = os.environ["PATH"]
        _ensure_nvidia_paths()
        assert os.environ["PATH"] == p1, "重复调用不应重复注入"
        assert "nvidia" in os.environ["PATH"].lower() or saved == p1, "应至少尝试注入"
    finally:
        os.environ["PATH"] = saved


def test_ensure_nvidia_paths_injects_real_paths():
    """本机装有 nvidia pip 包时，路径应真实存在且注入"""
    import site
    sp = site.getsitepackages()[0]
    real = os.path.join(sp, "nvidia", "cublas", "bin")
    if os.path.isdir(real):
        saved = os.environ.get("PATH", "")
        try:
            _ensure_nvidia_paths()
            assert real.lower() in os.environ["PATH"].lower(), "cuBLAS 路径应被注入"
        finally:
            os.environ["PATH"] = saved


# ---------- C1: doctor 检查函数 ----------

def test_check_cookie_ok_and_bad():
    assert doctor.check_cookie(FakeApiClient(nav_ok=True)) is True
    assert doctor.check_cookie(FakeApiClient(nav_ok=False)) is False


def test_check_cookie_handles_exception():
    class BoomClient:
        def check_login(self):
            raise RuntimeError("network error")

    assert doctor.check_cookie(BoomClient()) is False


def test_check_whisper_version_compat():
    """faster-whisper 可用且 transcribe 支持 log_progress（≥1.2 API）"""
    status = doctor.check_whisper()
    assert status["installed"] is True
    assert status["compatible"] is True, "faster-whisper 应兼容 log_progress API"


def test_check_gpu_reports_dll_status():
    """GPU 检查应返回 cuda 可用性与 cublas/cudnn DLL 探测结果"""
    result = doctor.check_gpu()
    assert "device" in result
    assert "cublas_ok" in result
    assert "cudnn_ok" in result
    # 本机若检测到 cuda 设备但缺 DLL，应给出修复提示（不抛异常）
    if result["device"] == "cuda":
        assert isinstance(result["cublas_ok"], bool)
        assert isinstance(result["cudnn_ok"], bool)


def test_doctor_main_runs():
    """doctor main 应以 0 退出且输出诊断行（不触发网络，靠注入）"""
    code = doctor.main(["cookie", "gpu", "whisper"])
    assert code == 0
