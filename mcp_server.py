"""MCP 单文件入口。

用法：在 AI 客户端的 MCP 配置里只写这个文件：
    {"command": "python", "args": ["D:/xxx/game-sprite-gen-mcp/mcp_server.py"]}

不需要 pip install 本项目，不需要 PYTHONPATH。首次运行自动补依赖；
缺少 config.json 时会提示用户新建。所有日志只写 stderr，不污染 MCP 的 stdio 协议。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
CONFIG = ROOT / "config.json"
PACKAGES = ("mcp>=1.2.0", "pillow>=10.0", "httpx>=0.27")


def _log(message: str) -> None:
    print(f"[spritegen] {message}", file=sys.stderr, flush=True)


def _prepare() -> None:
    """定位源码和配置文件；缺 config.json 时明确告知用户怎么处理。"""
    sys.path.insert(0, str(SRC))
    if not CONFIG.is_file():
        _log(f"未找到 {CONFIG}")
        _log("请新建 config.json（内容参考 config.example.json），或把 config.example.json 改名为 config.json，然后填写 apiKey")
        raise SystemExit(1)
    os.environ.setdefault("SPRITEGEN_CONFIG", str(CONFIG))


def _deps_ready() -> bool:
    try:
        import mcp  # noqa: F401
        import httpx  # noqa: F401
        from PIL import Image  # noqa: F401
    except ImportError:
        return False
    return True


def _install_deps() -> bool:
    _log("首次运行：正在安装依赖 mcp / pillow / httpx（需要联网）...")
    try:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "-q", *PACKAGES],
            stdout=sys.stderr,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        _log(f"自动安装失败：{exc}")
        _log(f'请手动运行："{sys.executable}" -m pip install mcp pillow httpx')
        return False
    return True


def main() -> None:
    _prepare()
    if not _deps_ready():
        if not _install_deps():
            raise SystemExit(1)
        # 用干净的进程重新加载依赖，避免失败导入残留在 sys.modules 里。
        os.execv(sys.executable, [sys.executable, str(Path(__file__).resolve()), *sys.argv[1:]])
    from spritegen.mcp_server import main as serve

    serve()


if __name__ == "__main__":
    main()
