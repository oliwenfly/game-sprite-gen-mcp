"""OpenAI 兼容的图片生成接口客户端。"""

from __future__ import annotations

import base64
import io

import httpx
from PIL import Image

from .config import Settings

# 部分第三方网关会拦截默认的 python-httpx UA，这里伪装成浏览器规避 403。
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"


class ImageAPIError(RuntimeError):
    """生图接口返回错误。"""


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
    """极简客户端，只覆盖 /v1/images/generations。"""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.settings.api_key}",
            "User-Agent": USER_AGENT,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

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

        透明背景由流水线在提示词里要求模型直出，这里不按 settings.transparent 改接口参数；
        background / output_format 只在调用方显式传入时透传。"""
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
            resp = client.post(endpoint, json=payload, headers=self._headers())
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


def load_image(data: bytes) -> Image.Image:
    """把图片字节解码为 RGBA 图像。"""
    return Image.open(io.BytesIO(data)).convert("RGBA")
