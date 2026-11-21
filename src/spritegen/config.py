"""配置解析：环境变量 > 配置文件 > 默认值。"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULTS = {
    "baseUrl": "https://api.openai.com",
    "model": "gpt-image-1",
    "editModel": "gpt-image-1",
    "quality": "auto",
    "outDir": "generated/sprites",
    "timeoutMs": 180000,
    "transparent": False,
}

CONFIG_ENV = "SPRITEGEN_CONFIG"
CONFIG_NAMES = ("spritegen.json", "config.json")


def config_path() -> Path | None:
    """定位配置文件：SPRITEGEN_CONFIG 指定路径，否则在当前目录按约定名查找。"""
    explicit = os.environ.get(CONFIG_ENV)
    if explicit:
        return Path(explicit).expanduser()
    for name in CONFIG_NAMES:
        candidate = Path.cwd() / name
        if candidate.is_file():
            return candidate
    return None


def _read_config() -> dict[str, Any]:
    """读取配置文件，缺失或损坏时返回空字典。"""
    path = config_path()
    if not path or not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _as_bool(value: Any, default: bool = False) -> bool:
    """把配置中的布尔值（允许 "true"/"false" 字符串）统一成 bool。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text == "true":
            return True
        if text == "false":
            return False
    return default


@dataclass(frozen=True)
class Settings:
    """运行时配置。"""

    api_key: str
    base_url: str
    model: str
    edit_model: str
    quality: str
    out_dir: str
    root_dir: Path
    timeout_s: float
    config_file: Path | None = None
    transparent: bool = False

    @property
    def api_base(self) -> str:
        """去掉末尾斜杠的接口根地址。"""
        return self.base_url.rstrip("/")

    def require_key(self) -> None:
        """校验 API Key 已配置，否则给出明确的配置指引。"""
        if self.api_key:
            return
        where = self.config_file or "spritegen.json"
        raise RuntimeError(
            "没有配置图片 API Key：设置环境变量 SPRITEGEN_API_KEY，"
            f"或把 config.example.json 复制为 {where} 并填写 apiKey"
        )


def load_settings(root: str | Path | None = None) -> Settings:
    """按优先级合并配置。"""
    user = _read_config()
    env = os.environ
    root_value = env.get("SPRITEGEN_ROOT") or user.get("rootDir") or root or Path.cwd()
    env_transparent = env.get("SPRITEGEN_TRANSPARENT")
    transparent = (
        _as_bool(env_transparent)
        if env_transparent is not None
        else _as_bool(user.get("transparent"))
    )
    return Settings(
        api_key=str(env.get("SPRITEGEN_API_KEY") or user.get("apiKey") or ""),
        base_url=str(env.get("SPRITEGEN_BASE_URL") or user.get("baseUrl") or DEFAULTS["baseUrl"]),
        model=str(env.get("SPRITEGEN_MODEL") or user.get("model") or DEFAULTS["model"]),
        edit_model=str(env.get("SPRITEGEN_EDIT_MODEL") or user.get("editModel") or DEFAULTS["editModel"]),
        quality=str(env.get("SPRITEGEN_QUALITY") or user.get("quality") or DEFAULTS["quality"]),
        out_dir=str(user.get("outDir") or DEFAULTS["outDir"]),
        root_dir=Path(root_value).expanduser().resolve(),
        timeout_s=float(env.get("SPRITEGEN_TIMEOUT_MS") or user.get("timeoutMs") or DEFAULTS["timeoutMs"]) / 1000.0,
        config_file=config_path(),
        transparent=transparent,
    )
