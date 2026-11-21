"""配置解析：环境变量 > 配置文件 > 默认值。"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULTS = {
    "baseUrl": "https://api.openai.com",
    "model": "gpt-image-2.5",
    "editModel": "gpt-image-2.5",
    "quality": "auto",
    "outDir": "generated/sprites",
    "timeoutMs": 180000,
    "transparent": False,
}

CONFIG_ENV = "SPRITEGEN_CONFIG"
CONFIG_NAMES = ("spritegen.json", "config.json")


def _search_dirs() -> list[Path]:
    """配置文件查找目录：当前目录。入口脚本会通过 SPRITEGEN_CONFIG 显式指定。"""
    return [Path.cwd()]


def config_path() -> Path | None:
    """定位配置文件：SPRITEGEN_CONFIG 指定路径，否则在约定目录按约定名查找。"""
    explicit = os.environ.get(CONFIG_ENV)
    if explicit:
        return Path(explicit).expanduser()
    for directory in _search_dirs():
        for name in CONFIG_NAMES:
            candidate = directory / name
            if candidate.is_file():
                return candidate
    return None


def config_hint() -> str:
    """配置文件缺失时给用户的落地指引。"""
    target = config_path() or _search_dirs()[-1] / "config.json"
    return (
        f"未找到配置文件 {target}：请新建 config.json（内容参考 config.example.json），"
        "或把 config.example.json 改名为 config.json，然后填写 apiKey"
    )


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
        if self.config_file is None:
            raise RuntimeError(config_hint())
        raise RuntimeError(f"配置文件 {self.config_file} 中没有 apiKey：请填写后重试")


def load_settings(root: str | Path | None = None) -> Settings:
    """按优先级合并配置。"""
    user = _read_config()
    env = os.environ
    config_file = config_path()
    root_value = root or (config_file.parent if config_file else Path.cwd())
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
        config_file=config_file,
        transparent=transparent,
    )
