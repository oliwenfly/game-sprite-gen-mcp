"""OpenAI 兼容的图片生成 / 编辑接口客户端。"""

from __future__ import annotations

import base64
import io
from pathlib import Path

import httpx
from PIL import Image

from .config import Settings

# 部分第三方网关会拦截默认的 python-httpx UA，这里伪装成浏览器规避 403。
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"

_MIME = {"PNG": "image/png", "JPEG": "image/jpeg", "GIF": "image/gif", "WEBP": "image/webp"}


class ImageAPIError(RuntimeError):
    """生图 / 改图接口返回错误。"""


def sniff_format(data: bytes) -> str:
    """按文件头魔数判断图片格式。"""
    if len(data) < 12:
        return "bin"
    if data[0] == 0x89 and data[1] == 0x50 and data[2] == 0x4E:
        return "png"
    if data[0] == 0xFF and data[1] == 0xD8:
        return "jpg"
    if data[:3] == b"GIF":
        return "gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return "bin"


def _decode_item(item: dict) -> bytes:
    """把接口返回项还原成图片字节。"""
    if item.get("b64_json"):
        return base64.b64decode(item["b64_json"])
    url = item.get("url")
    if not url:
        raise ImageAPIError("返回项既没有 b64_json 也没有 url")
    resp = httpx.get(url, headers={"User-Agent": USER_AGENT}, timeout=120, follow_redirects=True)
    resp.raise_for_status()
    return resp.content


class ImageClient:
    """极简客户端，只覆盖 /v1/images/generations 与 /v1/images/edits。"""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def _headers(self, json_body: bool) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self.settings.api_key}", "User-Agent": USER_AGENT}
        if json_body:
            headers["Content-Type"] = "application/json"
            headers["Accept"] = "application/json"
        return headers

    def _timeout(self) -> httpx.Timeout:
        return httpx.Timeout(self.settings.timeout_s)

    def generate(
        self,
        prompt: str,
        *,
        size: str | None = None,
        n: int = 1,
        background: str | None = None,
        output_format: str | None = None,
    ) -> tuple[list[bytes], str]:
        """文生图，返回图片字节列表与模型名。

        透明背景由提示词要求模型直出（见 config.TRANSPARENT_PROMPT），这里不再按
        settings.transparent 改接口参数；background / output_format 只在调用方显式传入时透传。"""
        payload: dict[str, object] = {
            "model": self.settings.model,
            "prompt": prompt,
            "quality": self.settings.quality,
        }
        if size:
            payload["size"] = size
        if n > 1:
            payload["n"] = n
        if background:
            payload["background"] = background
            # 透明背景只有 PNG 支持 alpha，显式要透明时补上输出格式。
            if background == "transparent" and not output_format:
                output_format = "png"
        if output_format:
            payload["output_format"] = output_format

        endpoint = f"{self.settings.api_base}/v1/images/generations"
        with httpx.Client(timeout=self._timeout(), follow_redirects=True) as client:
            resp = client.post(endpoint, json=payload, headers=self._headers(json_body=True))
        return self._collect(resp, endpoint, self.settings.model)

    def edit(
        self,
        prompt: str,
        images: list[Path],
        *,
        size: str | None = None,
    ) -> tuple[list[bytes], str]:
        """以参考图改图，返回图片字节列表与模型名。"""
        # 接口要求 multipart 上传，按文件头魔数推断 MIME，避免后缀与实际格式不符。
        files = []
        for path in images:
            data = path.read_bytes()
            fmt = sniff_format(data)
            mime = _MIME.get(fmt.upper(), "application/octet-stream")
            files.append(("image", (path.name, data, mime)))

        data_fields: dict[str, str] = {"model": self.settings.model, "prompt": prompt}
        if size:
            data_fields["size"] = size

        endpoint = f"{self.settings.api_base}/v1/images/edits"
        with httpx.Client(timeout=self._timeout(), follow_redirects=True) as client:
            resp = client.post(endpoint, data=data_fields, files=files, headers=self._headers(json_body=False))
        return self._collect(resp, endpoint, self.settings.model)

    @staticmethod
    def _collect(resp: httpx.Response, endpoint: str, model: str) -> tuple[list[bytes], str]:
        """校验响应并提取图片字节。"""
        if resp.status_code >= 400:
            body = resp.text[:500] or "(空响应)"
            raise ImageAPIError(f"接口返回 {resp.status_code}：{body}；端点 {endpoint}，模型 {model}")
        try:
            data = resp.json()
        except ValueError as exc:
            raise ImageAPIError(f"接口返回的不是 JSON：{resp.text[:300]}") from exc
        items = data.get("data") or []
        if not items:
            raise ImageAPIError(f"接口没有返回图片：{str(data)[:400]}")
        return [_decode_item(item) for item in items], str(data.get("model") or model)


def save_images(blobs: list[bytes], directory: Path, prefix: str = "img") -> list[Path]:
    """把图片字节写入目录，按内容自动确定扩展名。"""
    from datetime import datetime

    directory.mkdir(parents=True, exist_ok=True)
    # 时间戳精确到秒，同一批次的多张图再用序号区分，避免互相覆盖。
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    saved = []
    for index, data in enumerate(blobs):
        suffix = f"-{index + 1}" if len(blobs) > 1 else ""
        path = directory / f"{prefix}-{stamp}{suffix}.{sniff_format(data)}"
        path.write_bytes(data)
        saved.append(path)
    return saved


def load_image(data: bytes) -> Image.Image:
    """把图片字节解码为 RGBA 图像。"""
    return Image.open(io.BytesIO(data)).convert("RGBA")
