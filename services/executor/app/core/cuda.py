"""Windows 下为 CTranslate2 注册 pip 安装的 CUDA DLL。"""

from __future__ import annotations

import contextlib
import os
import sys
from pathlib import Path

DLL_DIRS = (
    Path("nvidia") / "cublas" / "bin",
    Path("nvidia") / "cudnn" / "bin",
    Path("nvidia") / "cuda_nvrtc" / "bin",
)


def enable_cuda_dlls() -> None:
    if sys.platform != "win32":
        return
    try:
        import site

        roots = [Path(item) for item in site.getsitepackages()]
    except Exception:
        return
    for root in roots:
        for relative in DLL_DIRS:
            directory = root / relative
            if not directory.is_dir():
                continue
            with contextlib.suppress(AttributeError, OSError):
                os.add_dll_directory(str(directory))
            current = os.environ.get("PATH", "")
            if str(directory) not in current.split(os.pathsep):
                os.environ["PATH"] = str(directory) + os.pathsep + current
